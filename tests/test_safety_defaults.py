"""Safety defaults: allowlist, caps, principal name, quiet hours."""

from __future__ import annotations

import pytest

from fernruf.cli import main
from fernruf.quiet_hours import parse_quiet_hours
from fernruf.safety_limits import (
    MAX_CALLS_PER_DAY_CEILING,
    MAX_MINUTES_PER_DAY_CEILING,
    MAX_MONTHLY_BUDGET_EUR_CEILING,
)
from fernruf.settings import Settings, load_settings, validate_operational_settings
from click.testing import CliRunner


def test_env_cannot_raise_daily_call_cap() -> None:
    s = Settings(_env_file=None, calls_per_day=999)
    assert s.calls_per_day == MAX_CALLS_PER_DAY_CEILING


def test_env_cannot_raise_daily_minute_cap() -> None:
    s = Settings(_env_file=None, minutes_per_day=9999)
    assert s.minutes_per_day == MAX_MINUTES_PER_DAY_CEILING


def test_env_cannot_raise_monthly_budget() -> None:
    s = Settings(_env_file=None, monthly_budget_eur=9999.0)
    assert s.monthly_budget_eur == MAX_MONTHLY_BUDGET_EUR_CEILING


def test_quiet_hours_off_requires_explicit_flag() -> None:
    with pytest.raises(ValueError):
        parse_quiet_hours("off", allow_off=False)
    spec = parse_quiet_hours("off", allow_off=True)
    assert spec.enabled is False


def test_default_quiet_hours_enabled() -> None:
    spec = Settings(_env_file=None).quiet_hours_spec
    assert spec.enabled is True


def test_missing_principal_refuses_operational_start() -> None:
    settings = Settings(_env_file=None, principal_name="")
    with pytest.raises(SystemExit):
        validate_operational_settings(settings)


def test_cli_policy_check_always_enforces_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "")
    monkeypatch.setenv("FERNRUF_ALLOWLIST_ENABLED", "false")
    result = CliRunner().invoke(main, ["policy-check", "+493023125123"])
    assert result.exit_code == 2
    assert "allowlist" in result.output.lower() or "REJECT" in result.output


def test_load_settings_can_require_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FERNRUF_PRINCIPAL_NAME", raising=False)
    with pytest.raises(SystemExit):
        load_settings(require_principal=True)
