from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

_QUIET_HOURS_RE = re.compile(
    r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)\s*$"
)


@dataclass(frozen=True)
class QuietHoursSpec:
    """Owner-configurable quiet window in Europe/Berlin."""

    enabled: bool
    start: time | None
    end: time | None
    timezone: ZoneInfo
    raw: str

    def display(self) -> str:
        if not self.enabled:
            return "off"
        return f"{self.raw} Europe/Berlin"

    def is_quiet(self, when: datetime) -> bool:
        if not self.enabled or self.start is None or self.end is None:
            return False
        local = when.astimezone(self.timezone)
        t = local.time()
        start, end = self.start, self.end
        if start > end:
            return t >= start or t < end
        if start == end:
            return True
        return start <= t < end


def parse_quiet_hours(
    raw: str,
    *,
    timezone: ZoneInfo = BERLIN,
    allow_off: bool = False,
) -> QuietHoursSpec:
    text = raw.strip() if raw is not None else ""
    if not text or text.lower() == "off":
        if not allow_off:
            raise ValueError(
                "FERNRUF_QUIET_HOURS=off is disabled by default. "
                "Set FERNRUF_QUIET_HOURS_ALLOW_OFF=true to permit turning quiet hours off."
            )
        return QuietHoursSpec(enabled=False, start=None, end=None, timezone=timezone, raw="off")

    m = _QUIET_HOURS_RE.match(text)
    if not m:
        raise ValueError(
            "FERNRUF_QUIET_HOURS must be 'off', empty (disabled), or HH:MM-HH:MM (24h), "
            f"got {raw!r}"
        )
    h1, m1, h2, m2 = (int(m.group(i)) for i in range(1, 5))
    start = time(h1, m1)
    end = time(h2, m2)
    normalized = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
    return QuietHoursSpec(enabled=True, start=start, end=end, timezone=timezone, raw=normalized)
