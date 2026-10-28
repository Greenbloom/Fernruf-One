from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
import websockets
from fastapi import FastAPI, WebSocket
from pathlib import Path

from fernruf.ari.client import AriSession
from fernruf.ari.teardown import ari_teardown_channels, shielded_ari_teardown
from fernruf.ari.websocket_endpoint import WS_MEDIA_ENDPOINT
from fernruf.quiet_hours import BERLIN
from fernruf.settings import Settings
from fernruf.voice.embedded_uvicorn import (
    embedded_uvicorn_config,
    start_embedded_uvicorn,
    stop_embedded_uvicorn,
)
from fernruf.voice.events import GLOBAL_EVENT_BUS
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.pipecat_runner import run_pipecat_on_websocket
from fernruf.voice.simulate import SimulateConversation

LOG = logging.getLogger("fernruf.ari.m2")


def _transcript_from_session(session: Any) -> list[dict[str, Any]]:
    """Lines the live pipeline already committed. Does not invent text."""
    from fernruf.voice.call_session import message_text

    pending = ""
    if hasattr(session, "in_flight_assistant_completion"):
        pending = session.in_flight_assistant_completion()
    if hasattr(session, "flush_unrecorded_assistant"):
        session.flush_unrecorded_assistant(pending)
    if hasattr(session, "append_final_spoken_lines"):
        session.append_final_spoken_lines()
    recorded = list(getattr(session, "transcript_lines", None) or [])
    if recorded:
        return recorded

    lines: list[dict[str, Any]] = []
    messages = getattr(session, "context_messages", None) or []
    t_ms = session.ms_since_answer() if hasattr(session, "ms_since_answer") else 0
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        text = message_text(msg)
        if not text:
            continue
        if role == "assistant":
            speaker = "agent"
        elif role == "user":
            speaker = "callee"
        else:
            continue
        if lines and lines[-1]["speaker"] == speaker and lines[-1]["text"] == text:
            continue
        if lines:
            t_ms = max(t_ms, int(lines[-1]["t_ms"]))
        lines.append({"t_ms": t_ms, "speaker": speaker, "text": text})
    return lines


class M2AriSession(AriSession):
    def send_dtmf(self, channel_id: str, digits: str) -> None:
        r = self._client.post(
            f"/channels/{quote(channel_id)}/dtmf",
            params={"dtmf": digits},
        )
        r.raise_for_status()

    def create_bridge(self) -> str:
        r = self._client.post("/bridges", params={"type": "mixing"})
        r.raise_for_status()
        return r.json()["id"]

    def add_to_bridge(self, bridge_id: str, channel_id: str) -> None:
        r = self._client.post(
            f"/bridges/{quote(bridge_id)}/addChannel",
            params={"channel": channel_id},
        )
        if r.status_code == 422:
            detail = (r.text or "").strip().replace("\n", " ")[:240]
            raise RuntimeError(
                f"ARI addChannel failed (422): channel {channel_id} not in Stasis — {detail}"
            )
        r.raise_for_status()

    async def run_m2_call(
        self,
        endpoint: str,
        *,
        caller_id: str,
        settings: Settings,
        task: str,
        max_minutes: int,
        simulate: bool = False,
        llm_model_override: str | None = None,
        llm_reasoning_override: str | None = None,  # LlmReasoningMode when set
        skip_llm_preflight: bool = False,
        shutdown: asyncio.Event | None = None,
        call_id: str | None = None,
        language: str | None = None,
        detection_text: str | None = None,
        goal: str = "",
        must_say: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        from fernruf.language import prepare_call_settings

        settings = await prepare_call_settings(
            settings,
            task,
            language=language,
            detection_text=detection_text,
        )
        from fernruf.stt.whisper import whisper_initial_prompt, whisper_language_code

        detected = getattr(settings, "_detected_language_label", None) or settings.call_language
        LOG.info(
            "call start detected_language=%s whisper_language=%s initial_prompt=%s",
            detected,
            whisper_language_code(settings),
            whisper_initial_prompt(settings),
        )
        if simulate or settings.voice_mock:
            sim = SimulateConversation(
                settings,
                task,
                llm_model_override=llm_model_override,
                language=language,
                detection_text=detection_text,
            )
            if call_id:
                sim.call_id = call_id
            result = await sim.run()
            transcript = [
                {"t_ms": ln.t_ms, "speaker": ln.speaker, "text": ln.text} for ln in result.transcript
            ]
            return {
                "mode": "simulate",
                "call_id": result.call_id,
                "answered": True,
                "transcript_lines": len(result.transcript),
                "transcript": transcript,
                "first_agent_line": result.transcript[0].text if result.transcript else "",
                "dtmf_sent": result.dtmf_sent,
                "median_latency": result.median,
                "duration_s": 0.0,
                "call_language": result.call_language,
                "llm_model": result.llm_model,
                "llm_model_source": result.llm_model_source,
            }

        from fernruf.call_settings.resolver import resolve_call_llm_model
        from fernruf.llm.preflight import LlmPreflightError, preflight_llm
        from fernruf.stt.whisper import SttPreloadError, prepare_stt_for_call

        try:
            stt_device = await asyncio.to_thread(prepare_stt_for_call, settings)
            LOG.info("STT preload ok device=%s", stt_device)
        except SttPreloadError as exc:
            LOG.error("%s", exc)
            raise RuntimeError(str(exc)) from exc

        from fernruf.voice.call_session import M2CallSession
        from fernruf.voice.m2_pipeline import _llm_tools
        from fernruf.voice.prompt import build_system_prompt

        session = M2CallSession()
        ari_channels: dict[str, str | None] = {
            "pstn": None,
            "ws": None,
            "bridge": None,
        }
        ari_teardown_lock = asyncio.Lock()
        ari_teardown_done = False

        async def send_dtmf(digits: str) -> None:
            pstn = ari_channels["pstn"]
            if pstn:
                await asyncio.to_thread(self.send_dtmf, pstn, digits)

        def _mark_pstn_teardown_done() -> None:
            nonlocal ari_teardown_done
            ari_teardown_done = True

        async def ari_teardown(*, reason: str = "hangup") -> None:
            async with ari_teardown_lock:
                skip_pstn = ari_teardown_done
            await shielded_ari_teardown(
                self,
                ari_channels,
                reason=reason,
                skip_pstn=skip_pstn,
                on_pstn_deleted=_mark_pstn_teardown_done,
            )

        async def hangup() -> None:
            await ari_teardown(reason="end_call")

        hooks = M2CallHooks(send_dtmf=send_dtmf, hangup=hangup, session=session)
        system = build_system_prompt(settings, task)
        preflight_tools = [] if settings.llm_mock else _llm_tools(hooks, session, settings)
        resolved = resolve_call_llm_model(settings, override=llm_model_override)
        call_reasoning_override = llm_reasoning_override
        call_llm_model_override = llm_model_override
        if not skip_llm_preflight:
            try:
                preflight_result = await preflight_llm(
                    settings,
                    model=resolved.model,
                    system_instruction=system,
                    tools=preflight_tools,
                )
                if preflight_result.reasoning_override is not None:
                    call_reasoning_override = preflight_result.reasoning_override
                if preflight_result.model_override:
                    call_llm_model_override = preflight_result.model_override
            except LlmPreflightError as exc:
                raise RuntimeError(str(exc)) from exc

        call_id = call_id or str(uuid.uuid4())[:8]
        from fernruf.call_record import remember_call

        remember_call(
            settings,
            call_id=call_id,
            call_language=settings.call_language,
            task=task,
            status="dialing",
        )
        app = FastAPI()
        ready = asyncio.Event()
        pipeline_finished = asyncio.Event()

        @app.websocket("/media")
        async def media_ws(websocket: WebSocket) -> None:
            await websocket.accept()
            ready.set()
            try:
                await run_pipecat_on_websocket(
                    websocket,
                    settings=settings,
                    task=task,
                    call_id=call_id,
                    hooks=hooks,
                    llm_model_override=call_llm_model_override,
                    llm_reasoning_override=call_reasoning_override,
                    external_shutdown=shutdown,
                    goal=goal,
                    must_say=must_say,
                )
            finally:
                pipeline_finished.set()

        config = embedded_uvicorn_config(
            app,
            host=settings.ws_listen_host,
            port=settings.ws_listen_port,
        )
        server, server_task = start_embedded_uvicorn(config)

        ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/ari/events?api_key={quote(self.user)}:{quote(self.password)}&app={self.app}"
        )
        dtmf: list[str] = []
        answered_at: float | None = None
        answered_wall: str | None = None
        channel_id: str | None = None
        bridge_id: str | None = None
        ws_channel_id: str | None = None

        def _sync_ari_channel_state() -> None:
            ari_channels["pstn"] = channel_id
            ari_channels["ws"] = ws_channel_id
            ari_channels["bridge"] = bridge_id
        start = time.monotonic()
        done = asyncio.Event()

        async with websockets.connect(ws_url) as ws:
            await asyncio.sleep(0.3)

            async def reader() -> None:
                nonlocal channel_id, answered_at, answered_wall, ws_channel_id
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
                        self.active_channel_id = cid
                        self.subscribe_channel(cid)
                        _sync_ari_channel_state()
                    elif et == "ChannelStateChange" and cid == channel_id:
                        if ch.get("state") == "Up" and answered_at is None:
                            answered_at = time.monotonic()
                            answered_wall = datetime.now(tz=BERLIN).isoformat()
                            session.mark_answered(answered_at)
                            GLOBAL_EVENT_BUS.emit(call_id, "state", state="answered")
                            bridge_id = self.create_bridge()
                            _sync_ari_channel_state()
                            self.add_to_bridge(bridge_id, channel_id)
                            wr = self._client.post(
                                "/channels",
                                params={
                                    "endpoint": WS_MEDIA_ENDPOINT,
                                    "app": self.app,
                                    "appArgs": f"m2,{call_id}",
                                },
                            )
                            wr.raise_for_status()
                            ws_channel_id = wr.json()["id"]
                            _sync_ari_channel_state()
                            await ready.wait()
                            self.add_to_bridge(bridge_id, ws_channel_id)
                            await asyncio.sleep(0.3)
                    elif et == "ChannelDtmfReceived" and cid == channel_id:
                        dtmf.append(str(ev.get("digit", "")))
                    elif et in ("StasisEnd", "ChannelDestroyed") and cid == channel_id:
                        if hooks.request_pipeline_shutdown:
                            asyncio.create_task(hooks.request_pipeline_shutdown())
                        done.set()

            read_task = asyncio.create_task(reader())
            r = self._client.post(
                "/channels",
                params={
                    "endpoint": endpoint,
                    "app": self.app,
                    "appArgs": f"m2,{call_id}",
                    "callerId": caller_id,
                },
            )
            r.raise_for_status()
            channel_id = r.json()["id"]
            self.active_channel_id = channel_id
            self.subscribe_channel(channel_id)
            _sync_ari_channel_state()
            deadline = start + max_minutes * 60
            try:
                while not done.is_set() and time.monotonic() < deadline:
                    if shutdown and shutdown.is_set():
                        LOG.info("shutdown requested; hanging up M2 call")
                        await ari_teardown(reason="signal")
                        done.set()
                        break
                    if pipeline_finished.is_set():
                        LOG.info("pipeline finished; tearing down ARI call")
                        await ari_teardown(reason="pipeline_finished")
                        done.set()
                        break
                    await asyncio.sleep(0.5)
                if not done.is_set():
                    await ari_teardown(reason="deadline")
            finally:
                done.set()
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task
                try:
                    await asyncio.wait_for(pipeline_finished.wait(), timeout=25.0)
                except asyncio.TimeoutError:
                    LOG.warning("pipecat pipeline did not finish within 25s after call end")
                    if hooks.request_pipeline_shutdown:
                        with contextlib.suppress(Exception):
                            await hooks.request_pipeline_shutdown()
                        try:
                            await asyncio.wait_for(pipeline_finished.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            LOG.warning("pipecat pipeline still running after forced shutdown")
                await stop_embedded_uvicorn(server, server_task, timeout=5.0)
                self.active_channel_id = None

        from fernruf.voice.latency import LatencyLog

        medians = LatencyLog.median_report_from_jsonl(
            Path(settings.latency_log_path),
            call_id=call_id,
        )
        call_log = Path("data/calls") / f"{call_id}.log"
        return {
            "mode": "pipecat",
            "call_id": call_id,
            "answered": answered_at is not None,
            "answered_at": answered_wall,
            "dtmf": dtmf,
            "duration_s": time.monotonic() - start,
            "median_latency": medians,
            "latency_simulated": False,
            "latency_log": str(settings.latency_log_path),
            "call_log": str(call_log),
            "call_language": settings.call_language,
            "transcript": _transcript_from_session(session),
        }


async def run_m2_test_call_with_signals(
    ari: M2AriSession,
    *,
    endpoint: str,
    caller_id: str,
    settings: Settings,
    task: str,
    max_minutes: int,
    llm_model_override: str | None,
    llm_reasoning_override: str | None = None,
    skip_llm_preflight: bool = False,
    call_id: str | None = None,
) -> dict[str, Any]:
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        if not shutdown.is_set():
            LOG.warning("signal received; ending M2 call")
            shutdown.set()
        else:
            raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

    try:
        return await ari.run_m2_call(
            endpoint,
            caller_id=caller_id,
            settings=settings,
            task=task,
            max_minutes=max_minutes,
            llm_model_override=llm_model_override,
            llm_reasoning_override=llm_reasoning_override,
            skip_llm_preflight=skip_llm_preflight,
            shutdown=shutdown,
            call_id=call_id,
        )
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
