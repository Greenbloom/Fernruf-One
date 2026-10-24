"""Per-call cost estimates (telephony rates are operator-supplied)."""

from __future__ import annotations

from dataclasses import dataclass

_MOBILE_PREFIXES = ("15", "16", "17")


def is_german_mobile(e164: str) -> bool:
    if not e164.startswith("+49") or len(e164) < 6:
        return False
    national = e164[3:]
    return national.startswith(_MOBILE_PREFIXES)


@dataclass(frozen=True)
class CostEstimate:
    mobile_eur: float
    ai_eur: float

    @property
    def total_eur(self) -> float:
        return self.mobile_eur + self.ai_eur


def estimate_cost(
    e164: str,
    duration_s: float,
    *,
    mobile_flat: bool,
    ai_eur_per_min: float,
    mobile_eur_per_min: float,
) -> CostEstimate:
    """Pro-rata estimate. Mobile telephony is 0 with a flat mobile plan or on a landline."""
    minutes = max(0.0, duration_s) / 60.0
    ai = minutes * ai_eur_per_min
    mobile = 0.0
    if is_german_mobile(e164) and not mobile_flat:
        mobile = minutes * mobile_eur_per_min
    return CostEstimate(mobile_eur=round(mobile, 4), ai_eur=round(ai, 4))
