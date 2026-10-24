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
from fernruf.voice.latency import LatencyLog
from fernruf.voice.m2_pipeline import build_m2_pipeline
from fernruf.voice.verify_orchestration import median_latency_report
from tests.m2_pipeline_harness import (
    cancel_asterisk_output_tasks,
    prime_transport_flow,
    unblock_asterisk_output,
    wire_transport_bot_stopped_to_guard,
)

# Real call 00:29 — STT misheard "Hi" as "Bye.", then corrections; LLM must not end_call yet.
TRANSCRIPT_0029 = [
    "Bye.",
    "Hi",
    "Ich habe Hi gesagt.",
]


class _Replay0029LLMHandler(BaseHTTPRequestHandler):
    llm_posts: list[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        messages = body.get("messages") or []
        _Replay0029LLMHandler.llm_posts.append(messages)
        users = [m for m in messages if m.get("role") == "user"]
        last = (users[-1].get("content") or "") if users else ""
        n_users = len(users)

        if n_users >= 2 and "verstehen" in last.lower():
            self._stream_tool_end_call(
                "Vielen Dank für den Testanruf. Auf Wiederhören!",
            )
            return

        if n_users == 1:
            merged = last.lower()
            assert "hi" in merged and "gesagt" in merged, merged
            self._stream_text(
                "Hallo! Schön, dass Sie da sind. Dies ist ein kurzer Testanruf. "
                "Können Sie mich gut verstehen?",
            )
            return

        self._stream_text("Alles klar, danke.")

    def _stream_text(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _stream_text_and_end_call(self, text: str, *, reason: str, goodbye: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks: list[dict[str, Any]] = [
            {"choices": [{"index": 0, "delta": {"content": text}}]},
        ]
        args = json.dumps({"reason": reason, "goodbye": goodbye}, ensure_ascii=False)
        chunks.extend(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_end",
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
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": args}}
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
        )
        for chunk in chunks:
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
                                    "id": "call_end",
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
    _Replay0029LLMHandler.llm_posts.clear()
    server = HTTPServer(("127.0.0.1", 0), _Replay0029LLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def test_replay_0029_merged_user_no_end_call_before_answer(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, base_url = _start_server()
    pcm = _fixture_pcm("replay0029", sample_rate=16000, duration_s=0.15)
    wav = tmp_path / "fish.wav"
    wav.write_bytes(pcm_to_wav(pcm, 16000))
    latency_path = tmp_path / "latency.jsonl"
    latency_log = LatencyLog(latency_path)
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="example/model-a",
        fish_tts_rest_fixture=str(wav),
        voice_mock=False,
        llm_mock=False,
        llm_filler_delay_s=5.0,
        min_user_turns_before_hangup=1,
        min_call_duration_before_hangup_s=0.0,
        auto_hangup_after_goodbye_s=0.05,
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
        call_id="replay0029",
        hooks=hooks,
        session=session,
        latency_log=latency_log,
    )
    unblock_asterisk_output(transport)
    wire_transport_bot_stopped_to_guard(transport, built.hangup)
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
        session.commit_assistant_turn(
            "Guten Tag, hier ist ein kurzer Testanruf.",
        )
        user_agg = built.context_pair.user()
        for line in TRANSCRIPT_0029:
            await user_agg.push_frame(
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": line}],
                    run_llm=(line == TRANSCRIPT_0029[-1]),
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.35)
        await asyncio.sleep(3.0)
        assert hangup.await_count == 0, "end_call must not run without callee answer yet"
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

    users = [
        m.get("content")
        for m in built.llm_context.messages
        if m.get("role") == "user"
    ]
    assert len(users) == 1
    merged = users[0].lower()
    assert "bye" in merged or "hi" in merged
    assert "gesagt" in merged

    report = median_latency_report(latency_log)
    assert report["turns"] >= 1.0
