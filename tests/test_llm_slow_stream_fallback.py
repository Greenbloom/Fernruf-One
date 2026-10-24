from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import AsyncMock

from pipecat.frames.frames import LLMTextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from fernruf.llm.service_factory import create_fernruf_llm_service
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools


class _SlowReasoningHandler(BaseHTTPRequestHandler):
    post_count: int = 0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        _SlowReasoningHandler.post_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        model = body.get("model", "")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        if model == "example/model-a":
            ping = {"choices": [{"index": 0, "delta": {}}]}
            self.wfile.write(f"data: {json.dumps(ping)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.55)
            return

        chunk = {
            "choices": [{"index": 0, "delta": {"content": "Hallo, ich höre Sie."}}],
            "model": model,
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _start_server() -> tuple[HTTPServer, str]:
    _SlowReasoningHandler.post_count = 0
    server = HTTPServer(("127.0.0.1", 0), _SlowReasoningHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def test_first_byte_without_liveness_switches_to_fallback_model() -> None:
    server, base_url = _start_server()
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="example/model-a",
        llm_fallback_model="example/model-b",
        llm_first_token_timeout_s=0.4,
    )
    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    tools = _llm_tools(hooks, session, settings)
    llm = create_fernruf_llm_service(
        settings,
        model="example/model-a",
        provider="openrouter",
        system_instruction="Test",
        session=session,
    )
    context = LLMContext(
        messages=[{"role": "user", "content": "Hallo?"}],
        tools=tools,
    )
    pushed: list = []

    async def _capture(frame, direction=None):  # noqa: ANN001
        pushed.append(frame)

    llm.push_frame = _capture  # type: ignore[method-assign]

    try:
        asyncio.run(llm._process_context(context))
    finally:
        server.shutdown()

    assert _SlowReasoningHandler.post_count == 2
    assert session.llm_used_fallback is True
    text_frames = [f for f in pushed if isinstance(f, LLMTextFrame)]
    assert text_frames and "Hallo" in text_frames[0].text
    assert not any(isinstance(f, TTSSpeakFrame) for f in pushed)


class _ReasoningThenContentHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        reasoning = {
            "choices": [{"index": 0, "delta": {"reasoning": "plan"}}],
            "model": "example/model-a",
        }
        self.wfile.write(f"data: {json.dumps(reasoning)}\n\n".encode())
        time.sleep(0.05)
        content = {
            "choices": [{"index": 0, "delta": {"content": "Alles klar."}}],
            "model": "example/model-a",
        }
        self.wfile.write(f"data: {json.dumps(content)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def test_reasoning_delta_counts_as_first_token_liveness() -> None:
    server = HTTPServer(("127.0.0.1", 0), _ReasoningThenContentHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="example/model-a",
        llm_fallback_model="example/model-b",
        llm_first_token_timeout_s=0.2,
    )
    session = M2CallSession()
    llm = create_fernruf_llm_service(
        settings,
        model="example/model-a",
        provider="openrouter",
        system_instruction="Test",
        session=session,
    )
    context = LLMContext(messages=[{"role": "user", "content": "ping"}], tools=[])
    pushed: list = []

    async def _capture(frame, direction=None):  # noqa: ANN001
        pushed.append(frame)

    llm.push_frame = _capture  # type: ignore[method-assign]
    try:
        asyncio.run(llm._process_context(context))
    finally:
        server.shutdown()

    assert session.llm_used_fallback is False
    text_frames = [f for f in pushed if isinstance(f, LLMTextFrame)]
    assert text_frames and "Alles klar" in text_frames[0].text
