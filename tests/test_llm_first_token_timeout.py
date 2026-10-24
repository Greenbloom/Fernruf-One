from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from fernruf.llm.service_factory import create_fernruf_llm_service
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools


class _SlowFirstTokenHandler(BaseHTTPRequestHandler):
    post_count: int = 0
    slow_s: float = 2.0

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        _SlowFirstTokenHandler.post_count += 1
        n = _SlowFirstTokenHandler.post_count
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        assert body.get("stream") is True
        tools = body.get("tools")
        assert tools, "expected tools on request body"
        model = body.get("model", "")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if model == "test/model":
            ping = {"choices": [{"index": 0, "delta": {}}]}
            self.wfile.write(f"data: {json.dumps(ping)}\n\n".encode())
            self.wfile.flush()
            time.sleep(self.slow_s)
            return
        if model == "example/model-b":
            chunk = {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "t1",
                                    "type": "function",
                                    "function": {"name": "send_dtmf", "arguments": '{"digits":"1"}'},
                                }
                            ]
                        },
                    }
                ]
            }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _start_server(*, slow_s: float = 2.0) -> tuple[HTTPServer, str]:
    _SlowFirstTokenHandler.post_count = 0
    _SlowFirstTokenHandler.slow_s = slow_s
    server = HTTPServer(("127.0.0.1", 0), _SlowFirstTokenHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def test_first_token_timeout_with_tools_retries_and_no_orphan_tool_call() -> None:
    server, base_url = _start_server(slow_s=2.0)
    settings = Settings(
        _env_file=None,
        llm_base_url=base_url,
        llm_api_key="test-key",
        llm_model="test/model",
        llm_fallback_model="example/model-b",
        llm_first_token_timeout_s=0.4,
    )
    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    tools = _llm_tools(hooks, session, settings)
    llm = create_fernruf_llm_service(
        settings,
        model="test/model",
        provider="openrouter",
        system_instruction="Test",
        session=session,
    )
    context = LLMContext(
        messages=[{"role": "user", "content": "Drücke eins."}],
        tools=tools,
    )
    fn_calls: list = []

    async def _capture_function_calls(calls):  # noqa: ANN001
        fn_calls.append(calls)

    llm.run_function_calls = _capture_function_calls  # type: ignore[method-assign]

    try:
        asyncio.run(llm._process_context(context))
    finally:
        server.shutdown()

    assert _SlowFirstTokenHandler.post_count == 2
    assert session.llm_used_fallback is True
    assert len(fn_calls) == 1
    assert fn_calls[0][0].function_name == "send_dtmf"
    assert fn_calls[0][0].arguments == {"digits": "1"}
