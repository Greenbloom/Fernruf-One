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
    TTSStoppedFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.workers.runner import WorkerRunner
from pipecat_asterisk import AsteriskWebsocketTransport

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools, build_m2_pipeline
from pipecat.frames.frames import FunctionCallResultProperties


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


class _EndCallToolLLMHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _EndCallToolLLMHandler.requests.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
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
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": json.dumps(
                                            {
                                                "reason": "Test beendet",
                                                "goodbye": (
                                                    "Vielen Dank für den Test. Auf Wiederhören!"
                                                ),
                                            },
                                            ensure_ascii=False,
                                        )
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


def _run_server() -> tuple[HTTPServer, str]:
    _EndCallToolLLMHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _EndCallToolLLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def test_end_call_tool_result_skips_follow_up_llm() -> None:
    session = M2CallSession()
    session.note_user_transcript("Ja, das war der Test.")
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=1,
        min_call_duration_before_hangup_s=0.0,
    )
    hangup = AsyncMock()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    seen: list = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        seen.append((result, properties))

    params = MagicMock()
    params.result_callback = result_callback

    asyncio.run(
        end_call(
            params,
            reason="Test",
            goodbye="Vielen Dank für den Test. Auf Wiederhören!",
        )
    )
    assert seen[0][0]["ok"] is True
    props = seen[0][1]
    assert isinstance(props, FunctionCallResultProperties)
    assert props.run_llm is False
    assert session.pending_tool_tts == ["Vielen Dank für den Test. Auf Wiederhören!"]


def test_end_call_goodbye_tts_then_hangup_no_second_llm(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from fernruf.tts.audio import _fixture_pcm
    from fernruf.tts.rest_stream import pcm_to_wav

    server, base_url = _run_server()
    pcm = _fixture_pcm("bye", sample_rate=16000, duration_s=0.25)
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
        min_user_turns_before_hangup=1,
        min_call_duration_before_hangup_s=0.0,
        disclosure_prefix="",
        llm_first_token_timeout_s=0.0,
    )
    session = M2CallSession()
    session.note_user_transcript("Ja, das war der Test.")
    hangup = AsyncMock()
    shutdown = AsyncMock()
    hooks = M2CallHooks(
        send_dtmf=AsyncMock(),
        hangup=hangup,
        session=session,
        request_pipeline_shutdown=shutdown,
    )
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()
    transport = AsteriskWebsocketTransport(websocket=ws)
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task="test",
        call_id="endcall1",
        hooks=hooks,
        session=session,
    )
    _unblock_asterisk_output(transport)
    caplog.set_level(logging.INFO)

    async def run() -> None:
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
        await asyncio.sleep(0.3)
        await built.context_pair.user().push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "user", "content": "Du kannst auflegen."}],
                run_llm=True,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(2.5)
        await worker.cancel()
        await asyncio.wait_for(run_task, timeout=12.0)

    try:
        asyncio.run(run())
    finally:
        server.shutdown()

    llm_requests = len(_EndCallToolLLMHandler.requests)
    assert llm_requests >= 1, f"expected at least one LLM call, got {llm_requests}"
    tts_starts = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
    assert any("Vielen Dank für den Test" in msg for msg in tts_starts), tts_starts
    assert hangup.await_count >= 1
