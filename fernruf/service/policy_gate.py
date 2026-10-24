"""Policy checks that run before any dial. Codes are stable for the MCP client."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from fernruf.dial import is_internal_star_dial, normalize_e164
from fernruf.policy import evaluate_call_policy
from fernruf.quiet_hours import QuietHoursSpec
from fernruf.service.costs import estimate_cost, is_german_mobile

_MARKETING = re.compile(
    r"(cold[\s-]?call|werbeanruf|kaltakquise|unsolicited advertising|"
    r"advertising call|marketing call|\bwerbung\b|\btelemarketing\b|"
    r"\bverkaufsanruf\b|\bgratis\s+test\b|\bfree trial call\b|"
    r"\bsales pitch\b|\bpromotional call\b)",
    re.IGNORECASE,
)
_PURPOSE = frozenset(
    {
        "marketing",
        "advertising",
        "werbung",
        "cold_call",
        "akquise",
        "spam",
        "telemarketing",
        "promotion",
        "sales",
    }
)


@dataclass(frozen=True)
class SpendSnapshot:
    calls_today: int = 0
    minutes_today: float = 0.0
    spent_eur: float = 0.0
    mobile_spent_eur: float = 0.0


@dataclass(frozen=True)
class PolicyLimits:
    allowlist: frozenset[str]
    international_enabled: bool
    quiet_hours: QuietHoursSpec
    max_minutes_ceiling: int
    calls_per_day: int
    minutes_per_day: float
    monthly_budget_eur: float
    mobile_budget_eur: float
    mobile_flat_enabled: bool
    ai_eur_per_min: float
    mobile_eur_per_min: float


@dataclass(frozen=True)
class ServiceDecision:
    allowed: bool
    error_code: str | None
    message: str
    checks: dict


def is_marketing_text(text: str) -> bool:
    return bool(_MARKETING.search(text or ""))


def is_marketing_task(task: dict) -> bool:
    purpose = str(task.get("purpose") or task.get("kind") or "").strip().lower()
    if purpose in _PURPOSE:
        return True
    blob = " ".join(
        str(task.get(key) or "")
        for key in ("goal", "context", "fallback", "purpose", "kind")
    )
    return is_marketing_text(blob)


def _reject(code: str, message: str, checks: dict) -> ServiceDecision:
    checks = {**checks, "allowed": False, "error_code": code}
    return ServiceDecision(False, code, message, checks)


def evaluate_service_policy(
    to: str,
    *,
    task: dict,
    requested_minutes: int,
    limits: PolicyLimits,
    spend: SpendSnapshot,
    line_ready: bool,
    now: datetime | None = None,
) -> ServiceDecision:
    """Every gate that can block a dial. ``line_ready`` is the Asterisk registration."""
    checks: dict = {
        "allowlist": "enforced",
        "allowlist_size": len(limits.allowlist),
        "international_enabled": limits.international_enabled,
        "quiet_hours": limits.quiet_hours.display(),
        "max_minutes_ceiling": limits.max_minutes_ceiling,
        "calls_per_day": limits.calls_per_day,
        "minutes_per_day": limits.minutes_per_day,
        "monthly_budget_eur": limits.monthly_budget_eur,
        "mobile_budget_eur": limits.mobile_budget_eur,
        "requested_minutes": requested_minutes,
    }
    if is_marketing_task(task):
        return _reject(
            "marketing_prohibited",
            "Marketing and cold advertising calls are not allowed.",
            checks,
        )
    raw = to.strip()
    if is_internal_star_dial(raw):
        return _reject(
            "internal_star_dial",
            "Star-code internal numbers are blocked.",
            checks,
        )
    if not limits.allowlist:
        return _reject(
            "allowlist_empty",
            "Allowlist is empty, so no number can be called. "
            "On the host run: fernruf settings allow-number +49…",
            checks,
        )
    try:
        e164 = normalize_e164(raw)
    except ValueError:
        return _reject("invalid_number", "Number must be E.164, for example +49151…", checks)

    number = evaluate_call_policy(
        e164,
        allowlist_enabled=True,
        allowlist=limits.allowlist,
        international_enabled=limits.international_enabled,
        max_minutes=requested_minutes,
        quiet_hours=limits.quiet_hours,
        now=now,
        raw_input=raw,
    )
    if not number.allowed:
        reason = number.reason or "rejected_by_policy"
        if reason.startswith("blocked_prefix"):
            return _reject(
                "blocked_prefix",
                f"Premium and service numbers are blocked ({reason}).",
                checks,
            )
        messages = {
            "not_on_allowlist": "Number is not on the allowlist.",
            "international_disabled": "Only +49 numbers are allowed until international calls are enabled on the host.",
            "quiet_hours": f"Quiet hours are in effect ({number.quiet_hours}).",
            "max_minutes_out_of_range": "max_minutes must be between 1 and 30.",
            "internal_star_dial": "Star-code internal numbers are blocked.",
        }
        return _reject(reason, messages.get(reason, reason), checks)

    if requested_minutes > limits.max_minutes_ceiling:
        return _reject(
            "max_minutes_above_ceiling",
            f"max_minutes {requested_minutes} is above the ceiling of {limits.max_minutes_ceiling}.",
            checks,
        )
    if spend.calls_today >= limits.calls_per_day:
        return _reject(
            "daily_calls",
            f"Daily call cap reached ({limits.calls_per_day}).",
            checks,
        )
    if spend.minutes_today + requested_minutes > limits.minutes_per_day:
        return _reject(
            "daily_minutes",
            f"Daily minute cap is {limits.minutes_per_day:g}; "
            f"{spend.minutes_today:g} already used.",
            checks,
        )

    projected = estimate_cost(
        e164,
        requested_minutes * 60,
        mobile_flat=limits.mobile_flat_enabled,
        ai_eur_per_min=limits.ai_eur_per_min,
        mobile_eur_per_min=limits.mobile_eur_per_min,
    )
    if spend.spent_eur + projected.total_eur > limits.monthly_budget_eur + 1e-9:
        return _reject(
            "monthly_budget",
            f"Monthly budget of {limits.monthly_budget_eur:.2f} EUR cannot cover this call.",
            checks,
        )
    if (
        is_german_mobile(e164)
        and not limits.mobile_flat_enabled
        and spend.mobile_spent_eur + projected.mobile_eur > limits.mobile_budget_eur + 1e-9
    ):
        return _reject(
            "mobile_budget",
            f"Mobile spend cap of {limits.mobile_budget_eur:.2f} EUR is reached.",
            checks,
        )
    if not line_ready:
        return _reject(
            "line_unavailable",
            "Asterisk is down or the SIP trunk is not registered.",
            checks,
        )
    checks["allowed"] = True
    checks["to"] = e164
    return ServiceDecision(True, None, "", checks)
