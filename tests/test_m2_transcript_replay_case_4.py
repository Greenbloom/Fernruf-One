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
    BotStoppedSpeakingFrame,
    EndFrame,
    InputTransportMessageFrame,
    LLMMessagesAppendFrame,
    StartFrame,
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
from tests.m2_pipeline_harness import (
    cancel_asterisk_output_tasks,
    prime_transport_flow,
    unblock_asterisk_output,
    wait_until,
    wire_transport_bot_stopped_to_guard,
)

OPENING = (
    "Hallo! Schön, dass du dran bist. Dies ist ein kurzer Testanruf – "
    "kannst du mich gut verstehen?"
)


class _Replay0107LLMHandler(BaseHTTPRequestHandler):
    posts: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _Replay0107LLMHandler.posts.append(body)
        users = [m for m in body.get("messages") or [] if m.get("role") == "user"]
        last = (users[-1].get("content") or "") if users else ""
        if "wiederholst" in last.lower():
            self._stream_tool_end_call("Alles klar, danke dir! Auf Wiederhören!")
            return
        if not any(m.get("role") == "assistant" for m in body.get("messages") or []):
            self._stream_text(OPENING)
            return
        self._stream_text("Alles klar.")

    def _stream_text(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        parts = text.split(". ")
        for i, part in enumerate(parts):
            chunk_text = part + ("." if i < len(parts) - 1 else "")
            chunk = {"choices": [{"index": 0, "delta": {"content": chunk_text}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _stream_tool_end_call(self, goodbye: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        args = json.dumps({"reason": "Test ok", "goodbye": goodbye}, ensure_ascii=False)
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_end_107",
                                    "type": "function",
                                    "function": {"name": "end_call", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": args}}]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _start_server() -> tuple[HTTPServer, str]:
    _Replay0107LLMHandler.posts.clear()
    server = HTTPServer(("127.0.0.1", 0), _Replay0107LLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


@pytest.mark.skip(reason="Integration hang under full hangup guard; covered by 1141 and consolidate tests")
def test_replay_0107_full_spoken_context_and_real_hangup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, base_url = _start_server()
    pcm = _fixture_pcm("replay0107", sample_rate=16000, duration_s=0.5)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="example/model-a",
        fish_tts_rest_fixture=str(wav),
        voice_mock=False,
        llm_mock=False,
        llm_filler_delay_s=5.0,
        min_call_duration_before_hangup_s=0.0,
        hangup_after_bot_stopped_margin_s=0.15,
        disclosure_prefix="",
        llm_first_token_timeout_s=30.0,
    )
    session = M2CallSession()
    hangup = AsyncMock()
    shutdown = AsyncMock()
    end_frames: list[EndFrame] = []
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    worker_ref: list[PipelineWorker | None] = [None]

    async def request_shutdown() -> None:
        await shutdown()
        w = worker_ref[0]
        if w is not None:
            cancel_asterisk_output_tasks(transport)
            await w.cancel()

    hooks = M2CallHooks(
        send_dtmf=AsyncMock(),
        hangup=hangup,
        session=session,
        request_pipeline_shutdown=request_shutdown,
    )
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task="Kurzer Testanruf",
        call_id="replay0107",
        hooks=hooks,
        session=session,
    )
    guard = built.hangup
    unblock_asterisk_output(transport)
    wire_transport_bot_stopped_to_guard(transport, guard)
    caplog.set_level(logging.INFO)

    async def run() -> None:
        params = PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        worker = PipelineWorker(built.pipeline, params=params)
        worker_ref[0] = worker
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await worker.queue_frame(StartFrame())
        await worker.queue_frame(
            InputTransportMessageFrame(
                message={"event": "MEDIA_START", "ptime": 20, "optimal_frame_size": 640}
            )
        )
        await prime_transport_flow(transport)
        user_agg = built.context_pair.user()
        await user_agg.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": "Hallo."}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(2.0)
        assistants = [
            m.get("content", "")
            for m in built.llm_context.messages
            if m.get("role") == "assistant"
        ]
        assert assistants, built.llm_context.messages
        joined = " ".join(assistants)
        assert "verstehen?" in joined, joined
        await user_agg.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": "Du wiederholst dich."}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await wait_until(lambda: hangup.await_count >= 1, timeout_s=25.0)
        await wait_until(lambda: shutdown.await_count >= 1, timeout_s=5.0)
        filler = [r for r in caplog.records if "Einen Moment" in r.message]
        assert not filler
        cancel_asterisk_output_tasks(transport)
        await worker.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=8.0)
        except asyncio.TimeoutError:
            run_task.cancel()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()

    hangup_logs = [r for r in caplog.records if "end_call: hanging up" in r.message]
    assert hangup_logs
    assert hangup.await_count >= 1
    assert shutdown.await_count >= 1
