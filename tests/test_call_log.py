from __future__ import annotations

from fernruf.voice.call_log import _redact_line


def test_redacts_phone_and_api_key() -> None:
    line = "call +491713920001 key=sk-abcdefghijklmnopqrstuvwxyz"
    out = _redact_line(line)
    assert "+49…XXXX" in out
    assert "sk-" not in out
    assert "REDACTED" in out
