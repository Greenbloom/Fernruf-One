from __future__ import annotations

from fernruf.voice.call_log import _redact_line


def test_redact_line_preserves_timestamps() -> None:
    line = "2025-10-26 13:56:00,935 INFO fernruf: end_call: hanging up"
    assert _redact_line(line) == line


def test_redact_line_masks_german_phone_numbers() -> None:
    line = "dialing +491713920002 from 03023125123"
    out = _redact_line(line)
    assert "+491713920002" not in out
    assert "03023125123" not in out
