"""Every dial gate. These tests do not touch SIP or the MCP transport."""

from __future__ import annotations

from datetime import datetime

import pytest

from fernruf.quiet_hours import BERLIN, parse_quiet_hours
from fernruf.service.policy_gate import PolicyLimits, SpendSnapshot, evaluate_service_policy

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
ALLOWED = "+493023125123"
MOBILE = "+491713920001"
LANDLINE = "+4930231251"


def limits(**overrides: object) -> PolicyLimits:
    base = dict(
        allowlist=frozenset({ALLOWED}),
        international_enabled=False,
        quiet_hours=parse_quiet_hours("off", allow_off=True),
        max_minutes_ceiling=10,
        calls_per_day=10,
        minutes_per_day=100,
        monthly_budget_eur=25.0,
        mobile_budget_eur=20.0,
        mobile_flat_enabled=False,
        ai_eur_per_min=0.01,
        mobile_eur_per_min=0.199,
    )
    base.update(overrides)
    return PolicyLimits(**base)


def decide(number: str, **kwargs: object):
    task = kwargs.pop("task", {"goal": "Ask if Tuesday works."})
    return evaluate_service_policy(
        number,
        task=task,  # type: ignore[arg-type]
        requested_minutes=int(kwargs.pop("requested_minutes", 10)),  # type: ignore[arg-type]
        limits=kwargs.pop("limits", limits()),  # type: ignore[arg-type]
        spend=kwargs.pop("spend", SpendSnapshot()),  # type: ignore[arg-type]
        line_ready=bool(kwargs.pop("line_ready", True)),
        now=kwargs.pop("now", NOON),  # type: ignore[arg-type]
    )


def test_allowlisted_german_number_passes() -> None:
    decision = decide(ALLOWED)
    assert decision.allowed
    assert decision.error_code is None


def test_empty_allowlist_blocks_every_number() -> None:
    decision = decide(ALLOWED, limits=limits(allowlist=frozenset()))
    assert decision.error_code == "allowlist_empty"


def test_number_off_the_allowlist_is_rejected() -> None:
    decision = decide("+493023125124")
    assert decision.error_code == "not_on_allowlist"


def test_international_blocked_by_default() -> None:
    decision = decide("+12025550100", limits=limits(allowlist=frozenset({"+12025550100"})))
    assert decision.error_code == "international_disabled"


def test_international_allowed_when_enabled_and_listed() -> None:
    decision = decide(
        "+12025550100",
        limits=limits(allowlist=frozenset({"+12025550100"}), international_enabled=True),
    )
    assert decision.allowed


@pytest.mark.parametrize(
    "number",
    ["+499001234567", "+491370000000", "+491800000000", "+497000000000", "+491180000000"],
)
def test_blocked_prefixes(number: str) -> None:
    decision = decide(number, limits=limits(allowlist=frozenset({number}), international_enabled=True))
    assert decision.error_code == "blocked_prefix"


def test_internal_star_dial() -> None:
    decision = decide("**99")
    assert decision.error_code == "internal_star_dial"


def test_quiet_hours() -> None:
    decision = decide(
        ALLOWED,
        limits=limits(quiet_hours=parse_quiet_hours("20:00-08:00", allow_off=True)),
        now=datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN),
    )
    assert decision.error_code == "quiet_hours"


def test_max_minutes_range_and_ceiling() -> None:
    assert decide(ALLOWED, requested_minutes=0).error_code == "max_minutes_out_of_range"
    assert decide(ALLOWED, requested_minutes=31).error_code == "max_minutes_out_of_range"
    assert decide(ALLOWED, requested_minutes=15).error_code == "max_minutes_above_ceiling"


def test_daily_call_cap() -> None:
    decision = decide(ALLOWED, spend=SpendSnapshot(calls_today=10))
    assert decision.error_code == "daily_calls"


def test_daily_minute_cap() -> None:
    decision = decide(ALLOWED, spend=SpendSnapshot(minutes_today=95), requested_minutes=10)
    assert decision.error_code == "daily_minutes"


def test_monthly_budget_cap() -> None:
    decision = decide(ALLOWED, spend=SpendSnapshot(spent_eur=24.95), requested_minutes=10)
    assert decision.error_code == "monthly_budget"


def test_mobile_budget_blocks_mobile_not_landline() -> None:
    mobile = decide(
        MOBILE,
        limits=limits(allowlist=frozenset({MOBILE})),
        spend=SpendSnapshot(mobile_spent_eur=19.9),
        requested_minutes=10,
    )
    assert mobile.error_code == "mobile_budget"
    landline = decide(
        LANDLINE,
        limits=limits(allowlist=frozenset({LANDLINE})),
        spend=SpendSnapshot(mobile_spent_eur=19.9),
        requested_minutes=10,
    )
    assert landline.allowed


def test_mobile_flat_skips_mobile_cap_and_uses_ai_budget() -> None:
    decision = decide(
        MOBILE,
        limits=limits(
            allowlist=frozenset({MOBILE}),
            mobile_flat_enabled=True,
            monthly_budget_eur=10,
        ),
        spend=SpendSnapshot(mobile_spent_eur=19.9, spent_eur=0),
        requested_minutes=10,
    )
    assert decision.allowed


def test_line_down() -> None:
    decision = decide(ALLOWED, line_ready=False)
    assert decision.error_code == "line_unavailable"


@pytest.mark.parametrize(
    "task",
    [
        {"goal": "Kaltakquise für das neue Produkt."},
        {"goal": "Say hello", "purpose": "marketing"},
        {"goal": "This is a cold call about our offer."},
        {"goal": "Mach Werbung für unser neues Angebot am Telefon."},
        {"goal": "Follow up", "purpose": "sales"},
    ],
)
def test_marketing_is_prohibited(task: dict) -> None:
    assert decide(ALLOWED, task=task).error_code == "marketing_prohibited"


@pytest.mark.parametrize(
    "task",
    [
        {"goal": "Ask Vertrieb about invoice 4711 and when it will be paid."},
        {"goal": "Please cancel my newsletter subscription."},
        {"goal": "Schedule a product demo for the team next week."},
        {"goal": "Request a Prospekt by post for the solar offer."},
    ],
)
def test_operational_tasks_are_not_marketing(task: dict) -> None:
    assert decide(ALLOWED, task=task).allowed


def test_invalid_number() -> None:
    assert decide("12345").error_code == "invalid_number"
