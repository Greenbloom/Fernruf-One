"""Hard safety ceilings. Environment may only lower caps, never raise them."""

from __future__ import annotations

MAX_CALLS_PER_DAY_CEILING = 50
MAX_MINUTES_PER_DAY_CEILING = 300
MAX_MONTHLY_BUDGET_EUR_CEILING = 100.0


def clamp_calls_per_day(requested: int) -> int:
    return max(1, min(int(requested), MAX_CALLS_PER_DAY_CEILING))


def clamp_minutes_per_day(requested: int) -> int:
    return max(1, min(int(requested), MAX_MINUTES_PER_DAY_CEILING))


def clamp_monthly_budget_eur(requested: float) -> float:
    return max(0.0, min(float(requested), MAX_MONTHLY_BUDGET_EUR_CEILING))
