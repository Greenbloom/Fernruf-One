from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from tests.m3_support import service_settings

from fernruf.debug.trace import configure_trace, note
from fernruf.service.mcp_app import create_app


@pytest.fixture(autouse=True)
def _reset_debug_trace():
    configure_trace(enabled=False)
    yield
    configure_trace(enabled=False)


_SECRET = "sk-test-secret-value-not-real"


def _app(tmp_path, **overrides):
    settings, token = service_settings(tmp_path, **overrides)
    return create_app(settings), token


def test_debug_page_is_hidden_until_enabled(tmp_path) -> None:
    app, token = _app(tmp_path, debug_ui_enabled=False)
    with TestClient(app, client=("127.0.0.1", 9)) as client:
        assert client.get("/debug/").status_code == 404
        denied = client.get("/debug/calls", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 404


def test_debug_page_rejects_a_remote_peer(tmp_path) -> None:
    app, _token = _app(tmp_path, debug_ui_enabled=True)
    with TestClient(app, client=("203.0.113.5", 9)) as client:
        assert client.get("/debug/").status_code == 403


def test_debug_page_requires_the_monitor_token(tmp_path) -> None:
    app, token = _app(tmp_path, debug_ui_enabled=True)
    with TestClient(app, client=("127.0.0.1", 9)) as client:
        page = client.get("/debug/")
        assert page.status_code == 200
        assert "Fernruf debug" in page.text
        assert token not in page.text
        assert client.get("/debug/calls").status_code == 401
        ok = client.get("/debug/calls", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        assert ok.json() == {"calls": []}


def test_debug_socket_streams_a_redacted_event(tmp_path) -> None:
    app, token = _app(tmp_path, debug_ui_enabled=True)
    with TestClient(app, client=("127.0.0.1", 9)) as client:
        with client.websocket_connect("/debug/live") as socket:
            socket.send_json({"type": "auth", "token": token})
            snapshot = socket.receive_json()
            assert snapshot["type"] == "snapshot"
            note(
                call_id="call-1",
                kind="llm",
                name="openrouter/test-model",
                detail={"api_key": _SECRET, "tools": [{"name": "end_call"}]},
                to="+493000000000",
            )
            incoming = socket.receive_json()
    assert incoming["type"] == "event"
    event = incoming["event"]
    assert event["kind"] == "llm"
    assert event["detail"]["api_key"] == "[redacted]"
    assert _SECRET not in str(incoming)
    assert "+493000000000" not in str(incoming)
