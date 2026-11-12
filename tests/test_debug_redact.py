from __future__ import annotations

from fernruf.debug.redact import redact, redact_text
from fernruf.monitor.protocol import mask_e164

_SECRET = "sk-test-secret-value-not-real"
_PHONE = "+493000000000"


def test_secret_keys_and_bearer_values_are_removed() -> None:
    cleaned = redact(
        {
            "authorization": f"Bearer {_SECRET}",
            "api_key": _SECRET,
            "nested": {"token": _SECRET, "model": "openrouter/test-model"},
            "note": f"Authorization: Bearer {_SECRET}",
        }
    )
    blob = str(cleaned)
    assert _SECRET not in blob
    assert cleaned["authorization"] == "[redacted]"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["nested"]["token"] == "[redacted]"
    assert cleaned["nested"]["model"] == "openrouter/test-model"
    assert "[redacted]" in cleaned["note"]


def test_phone_numbers_use_the_monitor_mask() -> None:
    masked = mask_e164(_PHONE)
    assert redact({"to": _PHONE})["to"] == masked
    assert redact_text(f"calling {_PHONE} now") == f"calling {masked} now"
    assert _PHONE not in redact_text(f"calling {_PHONE} now")
