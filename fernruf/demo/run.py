"""Place the Fernruf call on the local demo extension and write the clip files."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fernruf.ari.m2 import M2AriSession, run_m2_test_call_with_signals
from fernruf.demo.artifacts import write_demo_texts
from fernruf.demo.callee import callee_listen_uri, serve_demo_callee
from fernruf.demo.guard import assert_local_demo_endpoint
from fernruf.demo.persona import apply_demo_caller_voice
from fernruf.demo.plan import DemoCallPlan
from fernruf.demo.recording import publish_recording
from fernruf.demo.video import render_demo_video, wav_duration_seconds
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.demo")


async def _wait_until_listening(host: str, port: int, *, timeout_s: float = 120.0) -> None:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    deadline = time.monotonic() + timeout_s
    last = "not started"
    while time.monotonic() < deadline:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, port),
                timeout=0.4,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            last = str(exc)
            await asyncio.sleep(0.05)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise RuntimeError(
        f"demo callee did not listen on {connect_host}:{port} ({last}). "
        f"Asterisk must dial {callee_listen_uri_host(connect_host, port)}"
    )


def callee_listen_uri_host(host: str, port: int) -> str:
    return f"ws://{host}:{port}/media"


async def run_demo_call(
    settings: Settings,
    *,
    task: str,
    persona: str,
    plan: DemoCallPlan,
    max_seconds: int,
    llm_model_override: str | None,
    llm_reasoning_override: str | None,
    video: bool,
) -> dict[str, Any]:
    settings = apply_demo_caller_voice(settings)
    endpoint = assert_local_demo_endpoint(plan.endpoint)
    if settings.demo_callee_ws_port == settings.ws_listen_port:
        raise RuntimeError(
            "FERNRUF_DEMO_CALLEE_WS_PORT must differ from FERNRUF_WS_PORT "
            "so the callee bot does not take the Fernruf media socket"
        )
    LOG.info("demo-call %s -> %s (local extension, SIP trunk not used)", plan.call_id, endpoint)
    LOG.info(
        "callee voice websocket %s (Asterisk fernruf-callee must use this URI)",
        callee_listen_uri(settings),
    )
    stop = asyncio.Event()
    server = asyncio.create_task(
        serve_demo_callee(settings, persona=persona, stop=stop),
        name="demo-callee",
    )
    result: dict[str, Any] = {}
    try:
        await _wait_until_listening(settings.ws_listen_host, settings.demo_callee_ws_port)
        if server.done():
            server.result()
        ari = M2AriSession(
            base_url=settings.asterisk_ari_url,
            user=settings.asterisk_ari_user,
            password=settings.asterisk_ari_password,
        )
        try:
            result = await run_m2_test_call_with_signals(
                ari,
                endpoint=endpoint,
                caller_id=settings.ari_caller_id,
                settings=settings,
                task=task,
                max_minutes=max(1, (int(max_seconds) + 59) // 60),
                max_seconds=float(max_seconds),
                llm_model_override=llm_model_override,
                llm_reasoning_override=llm_reasoning_override,
                skip_llm_preflight=True,
                call_id=plan.call_id,
            )
        finally:
            ari.close()
    finally:
        stop.set()
        try:
            await server
        except Exception:
            LOG.exception("demo callee server stopped with an error")

    transcript = list(result.get("transcript") or [])
    answered = bool(result.get("answered"))
    recording_error: str | None = None
    wav = None
    try:
        wav = await publish_recording(plan)
    except FileNotFoundError as exc:
        recording_error = str(exc)
        LOG.error("%s", exc)

    duration_ms = None
    if wav is not None:
        seconds = wav_duration_seconds(wav)
        if seconds is not None:
            duration_ms = int(seconds * 1000)
    texts = write_demo_texts(
        plan,
        task=task,
        persona=persona,
        transcript=transcript,
        duration_s=float(result.get("duration_s") or 0.0),
        answered=answered,
        duration_ms=duration_ms,
    )
    video_path = None
    video_error = None
    if video:
        if wav is None:
            video_error = "no mixed recording to render"
            LOG.error("%s", video_error)
        else:
            try:
                rendered = render_demo_video(
                    wav=wav,
                    mp4=plan.out / "demo.mp4",
                    turns=transcript,
                )
                video_path = str(rendered)
            except (FileNotFoundError, RuntimeError) as exc:
                video_error = str(exc)
                LOG.error("%s", exc)

    ok = answered and recording_error is None and video_error is None
    return {
        "ok": ok,
        "mode": "demo",
        "call_id": plan.call_id,
        "endpoint": endpoint,
        "answered": answered,
        "out": str(plan.out),
        "call_wav": str(plan.out / "call.wav") if wav is not None else None,
        "transcript": str(texts["transcript"]),
        "captions": str(texts["captions"]),
        "summary": str(texts["summary"]),
        "video": video_path,
        "recording_error": recording_error,
        "video_error": video_error,
    }
