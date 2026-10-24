from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import (
    InputTransportMessageFrame,
    LLMMessagesAppendFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.workers.runner import WorkerRunner
from pipecat_asterisk import AsteriskWebsocketTransport

from fernruf.settings import Settings
from fernruf.tts.audio import _fixture_pcm
from fernruf.tts.rest_stream import pcm_to_wav
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import build_m2_pipeline


class _FakeLLMHandler(BaseHTTPRequestHandler):
    stream_spec: list[dict[str, Any]] = []
    delay_s: float = 0.0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if not body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
            )
            return

        if self.delay_s:
            import time

            time.sleep(self.delay_s)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in self.stream_spec:
            payload = {
                "choices": [
                    {
                        "index": 0,
                        "delta": chunk.get("delta", {}),
                        "finish_reason": chunk.get("finish_reason"),
                    }
                ]
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


def _run_fake_llm_server(
    spec: list[dict[str, Any]], *, delay_s: float = 0.0
) -> tuple[HTTPServer, str]:
    _FakeLLMHandler.stream_spec = spec
    _FakeLLMHandler.delay_s = delay_s
    server = HTTPServer(("127.0.0.1", 0), _FakeLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def _unblock_asterisk_output(transport: AsteriskWebsocketTransport) -> None:
    out = transport.output()
    if getattr(out, "_client", None) is not None:
        out._client.send = AsyncMock()
    real_push = out.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        if isinstance(frame, TTSStoppedFrame) and getattr(out, "_flow_controller", None):
            out._flow_controller.bot_stopped_speaking_fence.set()
        await real_push(frame, direction)

    out.push_frame = push  # type: ignore[method-assign]


async def _drive_pipeline(
    built,
    transport: AsteriskWebsocketTransport,
    *,
    wait_s: float = 3.0,
) -> tuple[list[TTSAudioRawFrame], int]:
    audio_out: list[TTSAudioRawFrame] = []
    metrics = built.metrics
    real_push = metrics.push_frame

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        if isinstance(frame, TTSAudioRawFrame):
            audio_out.append(frame)
        await real_push(frame, direction)

    metrics.push_frame = capture  # type: ignore[method-assign]

    params = PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(built.pipeline, params=params)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())

    await worker.queue_frame(StartFrame())
    await worker.queue_frame(
        InputTransportMessageFrame(
            message={"event": "MEDIA_START", "ptime": 20, "optimal_frame_size": 640}
        )
    )
    await asyncio.sleep(0.4)
    out = transport.output()
    fc = getattr(out, "_flow_controller", None)
    if fc is not None:
        fc.bot_stopped_speaking_fence.set()
        task = getattr(fc, "_flow_control", None)
        if task is not None and not task.done():
            task.cancel()
    await built.context_pair.user().push_frame(
        LLMMessagesAppendFrame(
            messages=[{"role": "user", "content": "Hallo, wie geht es dir?"}],
            run_llm=True,
        ),
        FrameDirection.DOWNSTREAM,
    )
    await asyncio.sleep(wait_s)
    await worker.cancel()
    await asyncio.wait_for(run_task, timeout=12.0)
    return audio_out, metrics.outbound_bytes


@pytest.mark.parametrize(
    "stream_spec,expect_substring,wait_s",
    [
        (
            [
                {"delta": {"content": "Guten Tag, "}},
                {"delta": {"content": "ich höre Sie."}, "finish_reason": "stop"},
            ],
            "ich höre Sie",
            3.0,
        ),
        (
            [
                {"delta": {"reasoning": "thinking…"}},
                {"delta": {"content": "Alles klar."}, "finish_reason": "stop"},
            ],
            "Alles klar",
            3.0,
        ),
    ],
)
def test_build_m2_pipeline_llm_reply_reaches_tts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    stream_spec: list[dict[str, Any]],
    expect_substring: str,
    wait_s: float,
) -> None:
    """Would fail on e7fba8c (assistant aggregator before TTS swallowed LLM text)."""
    server, base_url = _run_fake_llm_server(stream_spec)
    pcm = _fixture_pcm("reply", sample_rate=16000, duration_s=0.4)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))

    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="test/model",
        fish_tts_rest_fixture=str(wav),
        voice_mock=False,
        llm_mock=False,
        llm_filler_delay_s=5.0,
        disclosure_prefix="",
    )

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task="test",
        call_id="e2e1",
        hooks=hooks,
        session=session,
    )

    caplog.set_level(logging.INFO, logger="fernruf.voice.tts")
    _unblock_asterisk_output(transport)
    try:
        audio_out, outbound_bytes = asyncio.run(_drive_pipeline(built, transport, wait_s=wait_s))
    finally:
        server.shutdown()

    tts_starts = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert any(expect_substring in msg for msg in tts_starts), tts_starts
    assert audio_out or outbound_bytes > 0, "expected TTSAudioRawFrame through pipeline"


def test_build_m2_pipeline_delayed_llm_stream(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server, base_url = _run_fake_llm_server(
        [{"delta": {"content": "Antwort nach Pause."}, "finish_reason": "stop"}],
        delay_s=0.0,
    )
    pcm = _fixture_pcm("x", sample_rate=16000, duration_s=0.3)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="test/model",
        fish_tts_rest_fixture=str(wav),
        voice_mock=False,
        llm_mock=False,
        disclosure_prefix="",
    )
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task="test",
        call_id="e2e2",
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=M2CallSession()),
        session=M2CallSession(),
    )
    caplog.set_level(logging.INFO, logger="fernruf.voice.tts")
    _unblock_asterisk_output(transport)
    try:
        asyncio.run(_drive_pipeline(built, transport, wait_s=3.0))
    finally:
        server.shutdown()
    assert any("Antwort nach Pause" in r.message for r in caplog.records if "TTS start" in r.message)
