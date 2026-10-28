"""Refuse a demo dial that would leave the machine via the SIP trunk."""

from __future__ import annotations

import re

_LOCAL = re.compile(r"Local/600-[0-9a-f]{8}@fernruf-demo/n")


class DemoTrunkError(RuntimeError):
    """demo-call would dial the SIP trunk or something other than the local extension."""


def assert_local_demo_endpoint(endpoint: str) -> str:
    cleaned = (endpoint or "").strip()
    lowered = cleaned.lower()
    if "sip-trunk" in lowered or "pjsip/" in lowered:
        raise DemoTrunkError(
            f"demo-call refuses to dial {cleaned!r}: that endpoint uses the SIP trunk"
        )
    if not _LOCAL.fullmatch(cleaned):
        raise DemoTrunkError(
            f"demo-call refuses to dial {cleaned!r}: "
            "only the local fernruf-demo extension is allowed"
        )
    return cleaned
