"""Apply MCP setting updates. Allowlist membership cannot be widened from MCP."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fernruf.audit import append_audit_event
from fernruf.call_settings.store import get_call_settings, update_call_settings
from fernruf.safety_limits import MAX_MONTHLY_BUDGET_EUR_CEILING
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.settings")

# A leaked MCP token must not widen who can be called. Those fields are CLI-only.
CLI_ONLY_FIELDS = frozenset(
    {
        "allowlist",
        "allowlist_enabled",
        "international_enabled",
        "mobile_flat_enabled",
        "opening_line",
        "quiet_hours",
    }
)
WRITABLE_FIELDS = frozenset(
    {
        "voice_reference_id",
        "persona_name",
        "default_language",
        "llm_model",
        "max_minutes",
        "monthly_budget_eur",
    }
)


def per_call_ceiling(settings: Settings) -> int:
    """Minutes the MCP and place_call may use. Env ceiling, and never above 30."""
    return max(1, min(30, int(settings.max_minutes), int(settings.max_minutes_cap)))


def monthly_ceiling(settings: Settings) -> float:
    return min(float(settings.monthly_budget_eur), MAX_MONTHLY_BUDGET_EUR_CEILING)


class SettingsUpdateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def apply_mcp_settings(settings: Settings, patch: dict[str, Any]) -> dict[str, Any]:
    """Update writable settings. Raises SettingsUpdateError and writes nothing on failure."""
    if not patch:
        raise SettingsUpdateError("invalid_settings", "No settings were provided.")
    forbidden = sorted(set(patch) & CLI_ONLY_FIELDS)
    if forbidden:
        raise SettingsUpdateError(
            "setting_requires_cli",
            "These settings can only be changed on the host CLI, not with the MCP token: "
            + ", ".join(forbidden)
            + ". Allowlist: fernruf settings allow-number / disallow-number.",
        )
    unknown = sorted(set(patch) - WRITABLE_FIELDS)
    if unknown:
        raise SettingsUpdateError(
            "unknown_setting",
            "Unknown or locked setting: " + ", ".join(unknown),
        )
    cleaned: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "max_minutes":
            minutes = int(value)
            ceiling = per_call_ceiling(settings)
            if minutes < 1 or minutes > 30:
                raise SettingsUpdateError(
                    "max_minutes_out_of_range",
                    "max_minutes must be between 1 and 30.",
                )
            if minutes > ceiling:
                raise SettingsUpdateError(
                    "above_ceiling",
                    f"max_minutes cannot be raised above the env ceiling of {ceiling}.",
                )
            cleaned[key] = minutes
        elif key == "monthly_budget_eur":
            amount = float(value)
            ceiling = monthly_ceiling(settings)
            if amount < 0:
                raise SettingsUpdateError("above_ceiling", "monthly_budget_eur cannot be negative.")
            if amount > ceiling + 1e-9:
                raise SettingsUpdateError(
                    "above_ceiling",
                    f"monthly_budget_eur cannot be raised above the env ceiling of {ceiling:.2f}.",
                )
            cleaned[key] = amount
        elif key == "default_language":
            code = str(value).strip().lower()
            if not code or len(code) > 16:
                raise SettingsUpdateError("invalid_settings", "default_language is invalid.")
            cleaned[key] = code
        elif key == "llm_model":
            cleaned[key] = str(value)
        else:
            cleaned[key] = value
    try:
        record = update_call_settings(cleaned, settings=settings)
    except ValueError as exc:
        raise SettingsUpdateError("invalid_settings", str(exc)) from exc
    LOG.info("fernruf settings update fields=%s", ",".join(sorted(cleaned)))
    append_audit_event(
        Path(settings.audit_path),
        {"event": "settings_update", "fields": sorted(cleaned)},
    )
    return record.to_public_dict()


def public_settings(settings: Settings) -> dict[str, Any]:
    return get_call_settings(settings).to_public_dict()
