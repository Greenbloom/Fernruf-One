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
)


class _Replay0031LLMHandler(BaseHTTPRequestHandler):
    posts: list[dict[str, Any]] = []
    end_call_count = 0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _Replay0031LLMHandler.posts.append(body)
        messages = body.get("messages") or []
        users = [m for m in messages if m.get("role") == "user"]
        last_user = (users[-1].get("content") or "") if users else ""

        if "höre echt gut" in last_user.lower() or "verstehe" in last_user.lower():
            self._stream_tool_end_call(
                "Alles klar, danke dir! Auf Wiederhören!",
            )
            return

        self._stream_text(
            "Super! Können Sie mich gut verstehen?",
        )

    def _stream_text(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _stream_tool_end_call(self, goodbye: str) -> None:
        _Replay0031LLMHandler.end_call_count += 1
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
                                    "id": "call_end_31",
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
    _Replay0031LLMHandler.posts.clear()
    _Replay0031LLMHandler.end_call_count = 0
    server = HTTPServer(("127.0.0.1", 0), _Replay0031LLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def test_replay_0031_goodbye_plays_before_hangup_single_end_call(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, base_url = _start_server()
    pcm = _fixture_pcm("replay0031", sample_rate=16000, duration_s=0.6)
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
        hangup_after_bot_stopped_margin_s=0.2,
        disclosure_prefix="",
        llm_first_token_timeout_s=30.0,
    )
    session = M2CallSession()
    hangup = AsyncMock()
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    worker_ref: list[PipelineWorker | None] = [None]
    async def request_shutdown() -> None:
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
        call_id="replay0031",
        hooks=hooks,
        session=session,
    )
    unblock_asterisk_output(transport)
    out = transport.output()
    real_push = out.push_frame

    async def delayed_bot_stopped(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        await real_push(frame, direction)
        if isinstance(frame, TTSStoppedFrame) and getattr(out, "_flow_controller", None):
            async def emit_bot_stopped() -> None:
                await asyncio.sleep(0.45)
                out._flow_controller.bot_stopped_speaking_fence.set()
                await built.metrics.push_frame(
                    BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
                )

            asyncio.create_task(emit_bot_stopped())

    out.push_frame = delayed_bot_stopped  # type: ignore[method-assign]
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
                messages=[{"role": "user", "content": "Ja, ich höre echt gut."}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await wait_until(lambda: hangup.await_count >= 1, timeout_s=30.0)
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

    assert _Replay0031LLMHandler.end_call_count >= 1
    goodbye_speaks = [
        r.message for r in caplog.records if r.message.startswith("TTS start:") and "Wiederhören" in r.message
    ]
    assert len(goodbye_speaks) == 1, goodbye_speaks
    assert hangup.await_count == 1
    tts_starts = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert any("Wiederhören" in msg for msg in tts_starts)
    hangup_logs = [i for i, r in enumerate(caplog.records) if "end_call: hanging up" in r.message]
    goodbye_logs = [i for i, r in enumerate(caplog.records) if "TTS start:" in r.message and "Wiederhören" in r.message]
    assert hangup_logs and goodbye_logs
    assert hangup_logs[0] > goodbye_logs[0]

    assistants = [
        m.get("content", "")
        for post in _Replay0031LLMHandler.posts
        for m in post.get("messages") or []
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    ]
    joined = " ".join(assistants).lower()
    assert joined.count("hallo") <= 2
    assert "hallo! alles klar" not in joined.replace(" ", "")
