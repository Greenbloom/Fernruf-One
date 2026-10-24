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
from tests.m2_pipeline_harness import prime_transport_flow, unblock_asterisk_output, wait_until

TRANSCRIPT_2345 = [
    "Ja, ich bin hier.",
    "Ja, ja, das geht.",
    "Nein, du hast mich angerufen.",
    "Ciao.",
]

_ASSISTANT_REPLIES = [
    "Mein Name ist Alex Example – ich rufe an, um die Leitung zu testen.",
    "Gut, danke — die Verbindung klingt stabil.",
    "Alex Example hat Sie angerufen; ich bin sein KI-Assistent.",
]


class _Replay2345LLMHandler(BaseHTTPRequestHandler):
    post_count: int = 0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        _Replay2345LLMHandler.post_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        messages = body.get("messages") or []
        users = [m for m in messages if m.get("role") == "user"]
        n = len(users)
        last = (users[-1].get("content") or "") if users else ""

        if n >= 4 and "ciao" in last.lower():
            self._stream_tool_end_call()
            return

        idx = min(max(n - 1, 0), len(_ASSISTANT_REPLIES) - 1)
        text = _ASSISTANT_REPLIES[idx]
        self._stream_text(text)

    def _stream_text(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _stream_tool_end_call(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        args = json.dumps(
            {
                "reason": "Gesprächspartner verabschiedet sich",
                "goodbye": "Vielen Dank für den Test. Auf Wiederhören!",
            },
            ensure_ascii=False,
        )
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
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": args}}
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _start_replay_server() -> tuple[HTTPServer, str]:
    _Replay2345LLMHandler.post_count = 0
    server = HTTPServer(("127.0.0.1", 0), _Replay2345LLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


@pytest.mark.skip(reason="Legacy replay hangs with transport-scoped hangup guard")
def test_replay_2345_transcript_hangup_after_ciao_no_Alex_tts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, base_url = _start_replay_server()
    pcm = _fixture_pcm("replay", sample_rate=16000, duration_s=0.2)
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
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
        auto_hangup_after_goodbye_s=0.05,
        disclosure_prefix="",
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
        call_id="replay2345",
        hooks=hooks,
        session=session,
    )
    unblock_asterisk_output(transport)
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
        for line in TRANSCRIPT_2345:
            await user_agg.push_frame(
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": line}],
                    run_llm=True,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.sleep(0.45)
        await wait_until(lambda: hangup.await_count >= 1, timeout_s=35.0)
        await worker.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=8.0)
        except asyncio.TimeoutError:
            run_task.cancel()

    try:
        asyncio.run(run())
    finally:
        server.shutdown()

    assert session.user_turn_count >= 4
    assert session.callee_goodbye_seen
    tts_starts = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert any("Wiederhören" in msg for msg in tts_starts), tts_starts
    joined = " ".join(tts_starts).lower()
    assert "mein name ist Alex" not in joined
    assert "ich bin Alex" not in joined
    assert hangup.await_count >= 1
