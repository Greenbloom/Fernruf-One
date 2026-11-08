"""Strip secrets and mask phone numbers before a value can be shown."""

from __future__ import annotations

import re
from typing import Any

from fernruf.monitor.protocol import mask_e164

_SECRET_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "api-key",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "secret",
        "password",
        "bearer",
        "mcp_token",
        "monitor_token",
        "webhook_secret",
        "github_client_secret",
        "cookie",
        "set-cookie",
        "set_cookie",
    }
)

_PHONE = re.compile(r"\+\d[\d\s().-]{6,}\d")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)\s*[=:]\s*\S+"
)
_KEY_PREFIX = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_\-]{8,}\b")

_PHONE_KEYS = frozenset(
    {
        "to",
        "number",
        "phone",
        "caller",
        "callee",
        "caller_id",
        "callerid",
        "endpoint",
    }
)


def _secret_key(name: str) -> bool:
    return name.strip().lower().replace(" ", "_") in _SECRET_KEYS


def redact_text(text: str) -> str:
    """Hide bearer values, assigned secrets, key-shaped tokens, and phone numbers."""
    cleaned = _BEARER.sub("Bearer [redacted]", text)
    cleaned = _ASSIGNED_SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", cleaned)
    cleaned = _KEY_PREFIX.sub("[redacted]", cleaned)

    def _mask(match: re.Match[str]) -> str:
        return mask_e164(match.group(0))

    return _PHONE.sub(_mask, cleaned)


def redact(value: Any) -> Any:
    """Return a copy safe to show. Secret keys become ``[redacted]``."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _secret_key(name):
                cleaned[name] = "[redacted]"
            elif name.strip().lower() in _PHONE_KEYS and isinstance(item, str):
                stripped = item.strip()
                if stripped.startswith("+") or stripped.isdigit():
                    cleaned[name] = mask_e164(stripped)
                else:
                    cleaned[name] = redact_text(item)
            else:
                cleaned[name] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))
