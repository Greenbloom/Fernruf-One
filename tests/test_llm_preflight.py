from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from fernruf.llm.config import LlmEndpoint
from fernruf.llm.preflight import LlmPreflightError, preflight_llm
from fernruf.settings import Settings


class _StreamingPreflightHandler(BaseHTTPRequestHandler):
    status: int = 200
    last_body: dict | None = None
    post_count: int = 0
    first_status: int | None = None
    first_error_message: str = (
        "Reasoning is mandatory for this endpoint and cannot be disabled."
    )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        _StreamingPreflightHandler.post_count += 1
        length = int(self.headers.get("Content-Length", "0"))
        _StreamingPreflightHandler.last_body = json.loads(self.rfile.read(length))
        if (
            _StreamingPreflightHandler.first_status is not None
            and _StreamingPreflightHandler.post_count == 1
        ):
            self.send_response(_StreamingPreflightHandler.first_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": {"message": _StreamingPreflightHandler.first_error_message}}
                ).encode()
            )
            return
        if self.status != 200:
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": {"message": "Model example/model-b is deprecated; switch to example/model-b"}}
                ).encode()
            )
            return

        body = _StreamingPreflightHandler.last_body or {}
        if not body.get("stream"):
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode())
            return

        self.send_response(self.status)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {
            "choices": [{"index": 0, "delta": {"content": "p"}, "finish_reason": None}]
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _start_server() -> tuple[HTTPServer, str]:
    _StreamingPreflightHandler.post_count = 0
    _StreamingPreflightHandler.first_status = None
    _StreamingPreflightHandler.last_body = None
    server = HTTPServer(("127.0.0.1", 0), _StreamingPreflightHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}/v1"


def test_preflight_raises_on_404() -> None:
    _StreamingPreflightHandler.status = 404
    server, base = _start_server()
    settings = Settings(
        _env_file=None,
        llm_base_url=base,
        llm_api_key="k",
        llm_model="example/model-b",
    )
    try:
        with pytest.raises(LlmPreflightError, match="404|preflight failed"):
            asyncio.run(
                preflight_llm(
                    settings,
                    system_instruction="sys",
                    tools=[],
                )
            )
    finally:
        server.shutdown()
        _StreamingPreflightHandler.status = 200


def test_preflight_uses_streaming_and_extra_body_reasoning() -> None:
    server, base = _start_server()
    settings = Settings(
        _env_file=None,
        llm_base_url=base,
        llm_api_key="k",
        llm_model="example/model-b",
        llm_reasoning="low",
    )
    ep = LlmEndpoint(
        base_url=base,
        api_key="k",
        model="example/model-b",
        provider="openrouter",
        default_headers={},
    )
    try:
        with patch(
            "fernruf.llm.preflight.resolve_llm_endpoint_for_call",
            return_value=(ep, MagicMock()),
        ):
            asyncio.run(
                preflight_llm(
                    settings,
                    system_instruction="Du bist ein Test-Agent.",
                    tools=[],
                )
            )
    finally:
        server.shutdown()

    body = _StreamingPreflightHandler.last_body or {}
    assert body.get("stream") is True
    assert body.get("reasoning") == {"effort": "low"}


def test_preflight_reasoning_off_retries_with_low_on_mandatory_400(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, base = _start_server()
    _StreamingPreflightHandler.first_status = 400
    settings = Settings(
        _env_file=None,
        llm_base_url=base,
        llm_api_key="k",
        llm_model="example/model-a",
        llm_reasoning="off",
    )
    ep = LlmEndpoint(
        base_url=base,
        api_key="k",
        model="example/model-a",
        provider="openrouter",
        default_headers={},
    )
    caplog.set_level("WARNING", logger="fernruf.llm.preflight")
    try:
        with patch(
            "fernruf.llm.preflight.resolve_llm_endpoint_for_call",
            return_value=(ep, MagicMock()),
        ):
            result = asyncio.run(
                preflight_llm(
                    settings,
                    system_instruction="sys",
                    tools=[],
                )
            )
    finally:
        server.shutdown()
        _StreamingPreflightHandler.first_status = None

    assert result.reasoning_override == "low"
    assert _StreamingPreflightHandler.post_count == 2
    assert any("reasoning effort low" in r.message.lower() for r in caplog.records)
    body = _StreamingPreflightHandler.last_body or {}
    assert body.get("reasoning") == {"effort": "low"}


def test_preflight_other_4xx_still_aborts_with_reasoning_off() -> None:
    _StreamingPreflightHandler.status = 403
    server, base = _start_server()
    settings = Settings(
        _env_file=None,
        llm_base_url=base,
        llm_api_key="k",
        llm_model="example/model-a",
        llm_reasoning="off",
    )
    ep = LlmEndpoint(
        base_url=base,
        api_key="k",
        model="example/model-a",
        provider="openrouter",
        default_headers={},
    )
    try:
        with patch(
            "fernruf.llm.preflight.resolve_llm_endpoint_for_call",
            return_value=(ep, MagicMock()),
        ):
            with pytest.raises(LlmPreflightError, match="preflight failed|403"):
                asyncio.run(
                    preflight_llm(
                        settings,
                        system_instruction="sys",
                        tools=[],
                    )
                )
    finally:
        server.shutdown()
        _StreamingPreflightHandler.status = 200
