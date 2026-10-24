"""Time-of-day greeting from Europe/Berlin, not the server clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

_PART_DE = {"morning": "Morgen", "afternoon": "Nachmittag", "evening": "Abend"}

_GREETINGS: dict[str, dict[str, str]] = {
    "de": {
        "morning": "Guten Morgen",
        "afternoon": "Guten Tag",
        "evening": "Guten Abend",
        "neutral": "Hallo",
    },
    "en": {
        "morning": "Good morning",
        "afternoon": "Good afternoon",
        "evening": "Good evening",
        "neutral": "Hello",
    },
    "zh": {
        "morning": "早上好",
        "afternoon": "下午好",
        "evening": "晚上好",
        "neutral": "您好",
    },
}


def part_of_day(when: datetime) -> str:
    """Morning before 12:00, afternoon before 18:00, evening after that. Berlin time."""
    hour = when.astimezone(BERLIN).hour
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def time_greeting(language: str, when: datetime | None = None) -> tuple[str, str]:
    """Return ``(time-of-day greeting, neutral hello)`` for the call language."""
    moment = when or datetime.now(BERLIN)
    part = part_of_day(moment)
    table = _GREETINGS.get((language or "de").split("-", 1)[0], _GREETINGS["en"])
    return table[part], table["neutral"]


def greeting_instruction(language: str, when: datetime | None = None) -> str:
    """One prompt line. Evening in Berlin is 18:00 and later, including 20:34."""
    moment = when or datetime.now(BERLIN)
    local = moment.astimezone(BERLIN)
    part = part_of_day(local)
    timed, neutral = time_greeting(language, local)
    return (
        f"Ortszeit Europe/Berlin: {local.strftime('%H:%M')} ({_PART_DE[part]}). "
        f"Wenn du mit der Tageszeit grüßt, sag „{timed}“. "
        f"Ein neutrales „{neutral}“ ist immer richtig. "
        "Keine andere Tageszeit."
    )
