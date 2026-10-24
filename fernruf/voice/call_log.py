from __future__ import annotations

import logging
import re
from pathlib import Path

# German-style phone numbers only — do not match ISO timestamps in log lines.
_PHONE = re.compile(
    r"(?:"
    r"\+49[\d\-()]{8,13}\d"
    r"|(?<!\d)0[1-9]\d{8,12}(?!\d)"
    r")"
)
_API_KEY = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}|api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._-]{8,})",
    re.IGNORECASE,
)


def _redact_line(text: str) -> str:
    out = _PHONE.sub(_mask_phone, text)
    out = _API_KEY.sub("[REDACTED]", out)
    return out


def _mask_phone(match: re.Match[str]) -> str:
    raw = re.sub(r"[^\d+]", "", match.group(0))
    if raw.startswith("+49"):
        return "+49…XXXX"
    if len(raw) >= 4:
        return raw[:2] + "…XXXX"
    return "…XXXX"


class RedactingCallLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = record.msg
        if isinstance(original, str):
            record.msg = _redact_line(original)
        if record.args:
            record.args = tuple(
                _redact_line(a) if isinstance(a, str) else a for a in record.args
            )
        line = super().format(record)
        record.msg = original
        return _redact_line(line)


def attach_call_log_handler(call_id: str, *, base_dir: Path | None = None) -> Path:
    """Mirror root logging for one call to ``data/calls/<call_id>.log``."""
    root = logging.getLogger()
    directory = base_dir or Path("data/calls")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{call_id}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        RedactingCallLogFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler._fernruf_call_id = call_id  # type: ignore[attr-defined]
    root.addHandler(handler)
    return path


def detach_call_log_handler(call_id: str) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_fernruf_call_id", None) == call_id:
            root.removeHandler(handler)
            handler.close()
