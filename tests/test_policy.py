from __future__ import annotations

from datetime import datetime

import pytest

from fernruf.dial import e164_to_sip_dial
from fernruf.policy import PolicyOutcome, evaluate_call_policy
from fernruf.quiet_hours import BERLIN, parse_quiet_hours


def test_e164_to_sip_german() -> None:
    assert e164_to_sip_dial("+493023125123") == "03023125123"


def test_e164_to_sip_international() -> None:
    assert e164_to_sip_dial("+349009911223") == "00" + "349009911223"


@pytest.mark.parametrize(
    "number,reason",
    [
        ("+499009999999", "blocked_prefix"),
        ("+491180000000", "blocked_prefix"),
        ("+491800000000", "blocked_prefix"),
        ("+491370000000", "blocked_prefix"),
        ("+497000000000", "blocked_prefix"),
    ],
)
def test_blocked_sonder(number: str, reason: str) -> None:
    d = evaluate_call_policy(
        number,
        allowlist_enabled=False,
        allowlist=frozenset(),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=parse_quiet_hours("off", allow_off=True),
        now=datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN),
    )
    assert d.outcome == PolicyOutcome.REJECT
    assert d.reason and reason in d.reason


def test_allowlist() -> None:
    d = evaluate_call_policy(
        "+493023125123",
        allowlist_enabled=True,
        allowlist=frozenset({"+493023125124"}),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=parse_quiet_hours("off", allow_off=True),
        now=datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN),
    )
    assert d.reason == "not_on_allowlist"


def test_quiet_hours_default_blocks_21_00() -> None:
    qh = parse_quiet_hours("20:00-08:00", allow_off=True)
    assert qh.is_quiet(datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN))
    d = evaluate_call_policy(
        "+493023125123",
        allowlist_enabled=False,
        allowlist=frozenset(),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=qh,
        now=datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN),
    )
    assert d.reason == "quiet_hours"
    assert "20:00-08:00" in d.quiet_hours


def test_quiet_hours_off_allows_21_00() -> None:
    qh = parse_quiet_hours("off", allow_off=True)
    assert not qh.is_quiet(datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN))
    d = evaluate_call_policy(
        "+493023125123",
        allowlist_enabled=False,
        allowlist=frozenset(),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=qh,
        now=datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN),
    )
    assert d.outcome == PolicyOutcome.ALLOW


def test_quiet_hours_custom_window() -> None:
    qh = parse_quiet_hours("22:00-06:00", allow_off=True)
    assert not qh.is_quiet(datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN))
    assert qh.is_quiet(datetime(2026, 6, 15, 23, 0, tzinfo=BERLIN))
    d = evaluate_call_policy(
        "+493023125123",
        allowlist_enabled=False,
        allowlist=frozenset(),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=qh,
        now=datetime(2026, 6, 15, 21, 0, tzinfo=BERLIN),
    )
    assert d.outcome == PolicyOutcome.ALLOW


def test_quiet_hours_invalid_raises() -> None:
    with pytest.raises(ValueError, match="FERNRUF_QUIET_HOURS"):
        parse_quiet_hours("not-a-window", allow_off=True)


def test_internal_double_star() -> None:
    d = evaluate_call_policy(
        "+493023125123",
        allowlist_enabled=False,
        allowlist=frozenset(),
        international_enabled=True,
        max_minutes=10,
        quiet_hours=parse_quiet_hours("off", allow_off=True),
        raw_input="**99",
    )
    assert d.reason == "internal_star_dial"
