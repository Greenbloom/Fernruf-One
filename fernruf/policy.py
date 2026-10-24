from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from fernruf.dial import is_internal_star_dial, normalize_e164
from fernruf.quiet_hours import BERLIN, QuietHoursSpec

BLOCKED_PREFIXES = (
    "0900",
    "0137",
    "0180",
    "0700",
)
BLOCKED_118_PATTERN = ("118",)  # 118xx


class PolicyOutcome(str, Enum):
    ALLOW = "allow"
    REJECT = "rejected_by_policy"


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str | None = None
    quiet_hours: str = "off"

    @property
    def allowed(self) -> bool:
        return self.outcome == PolicyOutcome.ALLOW


def is_blocked_sondernummer(sip_dial: str) -> str | None:
    n = sip_dial
    if n.startswith("00"):
        return None
    if not n.startswith("0"):
        n = "0" + n
    for prefix in BLOCKED_PREFIXES:
        if n.startswith(prefix):
            return prefix
    if n.startswith("118") or n.startswith("0118"):
        return "118xx"
    return None


def evaluate_call_policy(
    to_e164: str,
    *,
    allowlist_enabled: bool,
    allowlist: frozenset[str],
    international_enabled: bool,
    max_minutes: int,
    quiet_hours: QuietHoursSpec,
    now: datetime | None = None,
    raw_input: str | None = None,
) -> PolicyDecision:
    qh_display = quiet_hours.display()
    raw = raw_input if raw_input is not None else to_e164
    if is_internal_star_dial(raw):
        return PolicyDecision(PolicyOutcome.REJECT, "internal_star_dial", qh_display)

    try:
        e164 = normalize_e164(to_e164)
    except ValueError as exc:
        return PolicyDecision(PolicyOutcome.REJECT, str(exc), qh_display)

    if allowlist_enabled and e164 not in allowlist:
        return PolicyDecision(PolicyOutcome.REJECT, "not_on_allowlist", qh_display)

    if not international_enabled and not e164.startswith("+49"):
        return PolicyDecision(PolicyOutcome.REJECT, "international_disabled", qh_display)

    from fernruf.dial import e164_to_sip_dial

    sip_dial = e164_to_sip_dial(e164)
    blocked = is_blocked_sondernummer(sip_dial)
    if blocked:
        return PolicyDecision(PolicyOutcome.REJECT, f"blocked_prefix:{blocked}", qh_display)

    when = now or datetime.now(tz=BERLIN)
    if quiet_hours.is_quiet(when):
        return PolicyDecision(PolicyOutcome.REJECT, "quiet_hours", qh_display)

    if max_minutes < 1 or max_minutes > 30:
        return PolicyDecision(PolicyOutcome.REJECT, "max_minutes_out_of_range", qh_display)

    return PolicyDecision(PolicyOutcome.ALLOW, None, qh_display)
