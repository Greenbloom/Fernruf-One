from __future__ import annotations

import re

_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


def normalize_e164(raw: str) -> str:
    s = raw.strip().replace(" ", "").replace("-", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        raise ValueError(f"Expected E.164 with leading +, got {raw!r}")
    if not _E164.match(s):
        raise ValueError(f"Invalid E.164: {raw!r}")
    return s


def e164_to_sip_dial(e164: str) -> str:
    """Convert E.164 to a national/international dial string (+49… → 0…, other +CC… → 00CC…)."""
    n = normalize_e164(e164)
    if n.startswith("+49"):
        return "0" + n[3:]
    return "00" + n[1:]


def is_internal_star_dial(raw: str) -> bool:
    """True for star-code internal dials (e.g. ``**99`` on a home PBX)."""
    return raw.strip().startswith("**")
