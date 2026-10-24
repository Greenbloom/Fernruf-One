"""Shared fixtures for M3 tests. No SIP, no real tokens."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fernruf.settings import Settings

NOON = "the tests inject their own clock"


def fresh_token() -> str:
    return secrets.token_urlsafe(32)


def service_settings(tmp_path: Path, **overrides: object) -> tuple[Settings, str]:
    token = str(overrides.pop("token", fresh_token()))
    values: dict[str, object] = {
        "_env_file": None,
        "FERNRUF_MCP_TOKEN": token,
        "FERNRUF_SETTINGS_DB": str(tmp_path / "fernruf.sqlite"),
        "FERNRUF_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "FERNRUF_LATENCY_LOG": str(tmp_path / "latency.jsonl"),
        "FERNRUF_ALLOWLIST": "+493023125123",
        "FERNRUF_PRINCIPAL_NAME": "Alex Example",
        "quiet_hours": "off",
        "quiet_hours_allow_off": True,
        "FERNRUF_LLM_MOCK": True,
        "FERNRUF_VOICE_MOCK": True,
        "FERNRUF_M3_SIMULATE": True,
        "TYPESAFE_API_KEY": "",
        "FERNRUF_LLM_MODEL": "openrouter/test-model",
        "FERNRUF_INTERNATIONAL_ENABLED": False,
        "FERNRUF_NOTIFY_ENABLED": False,
        "FERNRUF_OAUTH_ISSUER": "https://fernruf.example.com",
        "FERNRUF_OAUTH_RESOURCE": "https://fernruf.example.com/mcp",
        "FERNRUF_GITHUB_CALLBACK_URL": "https://fernruf.example.com/oauth/github/callback",
        "FERNRUF_GITHUB_ALLOWED_USER_IDS": "99999999",
        "trusted_proxies": "127.0.0.1",
    }
    values.update(overrides)
    return Settings(**values), token


def tool_payload(result) -> object:
    text = result.content[0].text if result.content else ""
    if result.is_error:
        # The MCP SDK prefixes a deliberate ToolError with "Error executing tool <name>:".
        start = text.find("{")
        if start != -1:
            payload, _end = json.JSONDecoder().raw_decode(text[start:])
            return payload
        return json.loads(text)
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        # A list (or other non-object) return is wrapped so structuredContent stays an object.
        return structured["result"]
    if structured is not None:
        return structured
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
