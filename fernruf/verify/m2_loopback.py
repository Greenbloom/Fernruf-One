from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import websockets
from fastapi import FastAPI, WebSocket
from fernruf.ari.m2 import M2AriSession
from fernruf.ari.websocket_endpoint import WS_MEDIA_ENDPOINT
from fernruf.settings import Settings
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.pipecat_runner import run_pipecat_on_websocket
from fernruf.voice.processors.verify_flow import VerifyFlowState
from fernruf.voice.embedded_uvicorn import (
    embedded_uvicorn_config,
    start_embedded_uvicorn,
    stop_embedded_uvicorn,
)
from fernruf.voice.latency import LatencyLog

LOG = logging.getLogger("fernruf.verify.m2")


@dataclass
class M2VerifyLoopbackResult:
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    dtmf_sent: str = ""
    latency_path: str = ""
    call_id: str = ""
    duration_s: float = 0.0
    callee_ws_inbound_bytes: int = 0
    agent_ws_outbound_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_m2_verify_loopback(
    settings: Settings,
    *,
    endpoint: str,
    caller_id: str,
    task: str = "Kurzer Verifikationsanruf. Bestätige, dass du mich hörst.",
    max_minutes: int = 3,
) -> M2VerifyLoopbackResult:
    root = Path(__file__).resolve().parents[2]
    fish_fixture = root / "tests/fixtures/fish_tts_de.wav"
    dumped = settings.model_dump()
    dumped.update(
        {
            "llm_mock": True,
            "voice_mock": False,
            "fish_audio_api_key": "",
            "fish_tts_rest_fixture": str(fish_fixture),
        }
    )
    settings = Settings(**dumped)
    from fernruf.language import prepare_call_settings

    settings = await prepare_call_settings(settings, task)
    latency_path = Path(settings.latency_log_path)
    latency_log = LatencyLog(latency_path)
    state = VerifyFlowState()
    call_id = str(uuid.uuid4())[:8]
    ari = M2AriSession(
        base_url=settings.asterisk_ari_url,
        user=settings.asterisk_ari_user,
        password=settings.asterisk_ari_password,
    )
    pstn_channel_id: str | None = None
    errors: list[str] = []

    async def send_dtmf(digits: str) -> None:
        if pstn_channel_id:
            await asyncio.to_thread(ari.send_dtmf, pstn_channel_id, digits)

    async def hangup() -> None:
        if pstn_channel_id:
            await asyncio.to_thread(ari.hangup, pstn_channel_id)

    from fernruf.voice.call_session import M2CallSession

    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=send_dtmf, hangup=hangup, session=session)

    app = FastAPI()
    ws_accepted = asyncio.Event()
    media_ready = asyncio.Event()

    @app.websocket("/media")
    async def media_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        ws_accepted.set()
        await media_ready.wait()
        await run_pipecat_on_websocket(
            websocket,
            settings=settings,
            call_id=call_id,
            task=task,
            hooks=hooks,
            verify=True,
            verify_state=state,
        )

    config = embedded_uvicorn_config(
        app,
        host=settings.ws_listen_host,
        port=settings.ws_listen_port,
    )
    server, server_task = start_embedded_uvicorn(config)

    ws_url = (
        settings.asterisk_ari_url.replace("http://", "ws://").replace("https://", "wss://")
        + f"/ari/events?api_key={quote(settings.asterisk_ari_user)}:"
        f"{quote(settings.asterisk_ari_password)}&app={ari.app}"
    )
    start = time.monotonic()
    done = asyncio.Event()

    try:
        async with websockets.connect(ws_url) as ws:
            await asyncio.sleep(0.3)
            channel_id: str | None = None
            ws_channel_id: str | None = None
            answered_at: float | None = None

            async def reader() -> None:
                nonlocal channel_id, ws_channel_id, answered_at, pstn_channel_id
                while not done.is_set():
                    raw = await ws.recv()
                    ev: dict[str, Any] = json.loads(raw)
                    et = ev.get("type")
                    ch = ev.get("channel") or {}
                    cid = ch.get("id")
                    if channel_id and cid and cid not in (channel_id, ws_channel_id):
                        continue
                    if et == "StasisStart" and channel_id is None and cid:
                        channel_id = cid
                        pstn_channel_id = cid
                        ari.active_channel_id = cid
                        ari.subscribe_channel(cid)
                    elif et == "ChannelStateChange" and cid == channel_id:
                        if ch.get("state") == "Up" and answered_at is None:
                            answered_at = time.monotonic()
                            bridge_id = ari.create_bridge()
                            try:
                                ari.add_to_bridge(bridge_id, channel_id)
                            except RuntimeError as exc:
                                errors.append(str(exc))
                                done.set()
                                return
                            wr = ari._client.post(
                                "/channels",
                                params={
                                    "endpoint": WS_MEDIA_ENDPOINT,
                                    "app": ari.app,
                                    "appArgs": f"m2verify,{call_id}",
                                },
                            )
                            if wr.status_code >= 400:
                                detail = (wr.text or "").strip().replace("\n", " ")[:240]
                                errors.append(
                                    f"websocket originate failed: {wr.status_code} {detail}"
                                )
                                done.set()
                                return
                            ws_channel_id = wr.json()["id"]
                            try:
                                await asyncio.wait_for(ws_accepted.wait(), timeout=15.0)
                            except TimeoutError:
                                errors.append("websocket /media accept timed out (check FERNRUF_WS_URI)")
                                done.set()
                                return
                            try:
                                ari.add_to_bridge(bridge_id, ws_channel_id)
                            except RuntimeError as exc:
                                errors.append(str(exc))
                                done.set()
                                return
                            media_ready.set()
                            await asyncio.sleep(0.3)
                    elif et in ("StasisEnd", "ChannelDestroyed") and cid == channel_id:
                        done.set()

            read_task = asyncio.create_task(reader())
            r = ari._client.post(
                "/channels",
                params={
                    "endpoint": endpoint,
                    "app": ari.app,
                    "appArgs": f"m2verify,{call_id}",
                    "callerId": caller_id,
                },
            )
            if r.status_code >= 400:
                detail = (r.text or "").strip().replace("\n", " ")[:240]
                errors.append(f"PSTN originate failed: {r.status_code} {detail}")
                done.set()
            else:
                channel_id = r.json()["id"]
                pstn_channel_id = channel_id
                ari.active_channel_id = channel_id
                ari.subscribe_channel(channel_id)

            deadline = start + max_minutes * 60
            try:
                while not done.is_set() and not state.done.is_set():
                    if time.monotonic() > deadline:
                        errors.append("timeout")
                        break
                    await asyncio.sleep(0.2)
            finally:
                done.set()
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task
    finally:
        await stop_embedded_uvicorn(server, server_task)
        ari.close()

    checks = dict(state.checks)
    checks.setdefault("ai_notice_first", False)
    checks.setdefault("callee_rtp_received", False)
    checks.setdefault("callee_transcribed", False)
    checks.setdefault("barge_in", state.barge_in_fired)
    checks.setdefault("dtmf_sent", bool(state.dtmf_sent))
    checks.setdefault("agent_audio_out", False)
    checks.setdefault("latency_jsonl", latency_path.is_file() and latency_path.stat().st_size > 0)

    notice = (settings.disclosure_prefix or "").strip()
    has_disclosure = checks.get("ai_notice_text") or any(
        (notice and notice in (entry.get("text") or ""))
        or "KI-Assistent" in (entry.get("text") or "")
        or "AI assistant" in (entry.get("text") or "")
        for entry in state.transcript
    )
    checks["ai_notice_text"] = bool(has_disclosure)
    if not has_disclosure:
        errors.append("missing AI disclosure in agent speech")

    if state.checks.get("pipecat_errors") is False:
        errors.append("pipecat ERROR lines during M2 run")

    for needle in ("Danke für Ihre Zeit", "Vielen Dank"):
        if not any(needle in entry for entry in state.tts_synth_log):
            errors.append(f"assistant LLM line not synthesized by TTS: {needle!r}")

    required = [
        "ai_notice_first",
        "ai_notice_text",
        "callee_rtp_received",
        "callee_transcribed",
        "barge_in",
        "dtmf_sent",
        "latency_jsonl",
        "end_call_hangup",
        "agent_audio_out",
    ]
    for key in required:
        if not checks.get(key):
            errors.append(f"check failed: {key}")

    ok = not errors
    return M2VerifyLoopbackResult(
        ok=ok,
        checks=checks,
        transcript=state.transcript,
        dtmf_sent=state.dtmf_sent,
        latency_path=str(latency_path),
        call_id=call_id,
        duration_s=time.monotonic() - start,
        callee_ws_inbound_bytes=state.callee_ws_inbound_bytes,
        agent_ws_outbound_bytes=state.agent_ws_outbound_bytes,
        errors=errors,
    )
