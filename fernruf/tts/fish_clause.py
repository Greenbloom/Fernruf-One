from __future__ import annotations

import re

# Split only on comma/semicolon so en-dashes in German (e.g. „Testanruf – ist …“) stay intact.
_CLAUSE_BREAK = re.compile(r"([,;]\s*)")


def split_first_clause(text: str, *, min_words: int = 4) -> tuple[str | None, str]:
    """Return (first_clause, remainder) when an early clause boundary is found."""
    text = text.strip()
    if not text:
        return None, ""
    parts = _CLAUSE_BREAK.split(text, maxsplit=1)
    if len(parts) < 3:
        return None, text
    first = (parts[0] + parts[1]).strip()
    rest = parts[2].strip()
    words = [w for w in re.split(r"\s+", first) if w]
    if len(words) < min_words or not rest:
        return None, text
    return first, rest
