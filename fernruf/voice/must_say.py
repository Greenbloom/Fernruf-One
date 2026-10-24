"""must_say delivery. The first real agent turn has to say these lines."""

from __future__ import annotations

import re

_ASK_GOAL = re.compile(
    r"(?i)\b(ask|asking|inquire|find out|frag(?:e|en|t)?|erkundig\w*)\b"
)


def goal_needs_answer(goal: str) -> bool:
    """True when the goal is a question the callee still has to answer."""
    text = (goal or "").strip()
    if not text:
        return False
    if "?" in text:
        return True
    return _ASK_GOAL.search(text) is not None


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def missing_must_say(spoken: str, required: tuple[str, ...]) -> list[str]:
    """Required lines that are not already contained in ``spoken``."""
    hay = _fold(spoken)
    missing: list[str] = []
    for item in required:
        needle = _fold(item)
        if needle and needle not in hay:
            missing.append(item.strip())
    return missing


def supplement_must_say(spoken: str, required: tuple[str, ...]) -> str:
    """Append any must_say line the model left out of this turn."""
    missing = missing_must_say(spoken, required)
    if not missing:
        return spoken
    extra = " ".join(missing)
    base = (spoken or "").rstrip()
    if not base:
        return extra
    return f"{base} {extra}"


def brief_requirements(task: str) -> tuple[str, tuple[str, ...]]:
    """Goal and must_say from a formatted brief. Raw task text is the goal."""
    goal = ""
    must: list[str] = []
    for line in (task or "").splitlines():
        stripped = line.strip()
        lower = stripped.casefold()
        if lower.startswith("ziel:"):
            goal = stripped.split(":", 1)[1].strip()
        elif lower.startswith("muss sagen:"):
            rest = stripped.split(":", 1)[1]
            must.extend(part.strip() for part in rest.split(";") if part.strip())
    if not goal:
        goal = (task or "").strip()
    return goal, tuple(must)
