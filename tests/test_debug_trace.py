from __future__ import annotations

import json

import pytest

from fernruf.debug.trace import configure_trace, note
from fernruf.monitor.protocol import mask_e164

_SECRET = "sk-test-secret-value-not-real"
_PHONE = "+493000000000"


@pytest.fixture(autouse=True)
def _reset_debug_trace():
    configure_trace(enabled=False)
    yield
    configure_trace(enabled=False)


def test_disabled_trace_keeps_events_out_of_memory_logs_and_disk(tmp_path, caplog) -> None:
    path = tmp_path / "debug-ui.jsonl"
    configure_trace(enabled=False, persist=True, persist_path=path)
    with caplog.at_level("DEBUG"):
        missed = note(
            kind="llm",
            name="openrouter/test-model",
            detail={"api_key": _SECRET, "to": _PHONE},
            to=_PHONE,
        )
    assert missed is None
    assert not path.exists()
    assert _SECRET not in caplog.text
    assert _PHONE not in caplog.text


def test_capture_keeps_the_timeline_and_can_persist_only_when_asked(tmp_path) -> None:
    path = tmp_path / "debug-ui.jsonl"
    trace = configure_trace(enabled=True, persist=False, persist_path=path)
    note(
        call_id="call-1",
        kind="llm",
        name="openrouter/test-model",
        duration_ms=120,
        status="ok",
        detail={
            "api_key": _SECRET,
            "model": "openrouter/test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            "tools": [{"name": "end_call", "arguments": '{"reason": "task_complete"}'}],
        },
        to=_PHONE,
    )
    note(
        call_id="call-1",
        kind="tts",
        name="fish",
        duration_ms=80,
        detail={"text": "[warm] Hello.", "voice": "voice-demo", "fish": {"inline_tag": "[warm]"}},
    )
    note(call_id="call-1", kind="stt", name="partial", detail={"text": "Hel", "final": False})
    note(call_id="call-1", kind="stt", name="final", detail={"text": "Hello.", "final": True})
    note(
        call_id="call-1",
        kind="ari",
        name="DELETE",
        status="ok",
        detail={"path": "/channels/demo", "status_code": 204},
    )
    note(call_id="call-1", kind="mcp", name="place_call", duration_ms=15, to=_PHONE)
    note(
        call_id="call-1",
        kind="mood",
        name="freundlich",
        duration_ms=30,
        detail={
            "choice": "freundlich",
            "confidence": 0.8,
            "line": "Hello.",
            "fish": {"inline_tag": "[warm]", "latency": "normal"},
        },
    )
    calls = trace.snapshot()["calls"]
    assert len(calls) == 1
    assert calls[0]["to_masked"] == mask_e164(_PHONE)
    assert [event["kind"] for event in calls[0]["events"]] == [
        "llm",
        "tts",
        "stt",
        "stt",
        "ari",
        "mcp",
        "mood",
    ]
    llm = calls[0]["events"][0]
    assert llm["detail"]["api_key"] == "[redacted]"
    assert llm["detail"]["tools"][0]["name"] == "end_call"
    assert llm["detail"]["usage"]["prompt_tokens"] == 10
    assert calls[0]["events"][1]["detail"]["fish"]["inline_tag"] == "[warm]"
    assert calls[0]["events"][2]["detail"]["final"] is False
    assert calls[0]["events"][3]["detail"]["final"] is True
    blob = json.dumps(calls)
    assert _SECRET not in blob
    assert _PHONE not in blob
    assert not path.exists()

    persisted = configure_trace(enabled=True, persist=True, persist_path=path)
    persisted.note(
        call_id="call-2",
        kind="tts",
        name="fish",
        detail={"authorization": f"Bearer {_SECRET}", "text": "Hello."},
    )
    written = path.read_text(encoding="utf-8")
    assert _SECRET not in written
    assert "[redacted]" in written
    assert "Hello." in written
