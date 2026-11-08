"""Telephony lifecycle for the local monitor.

This is not the conversation phase in ``fernruf.voice.call_phase``. That object
decides whether the model may keep talking. This one is what a person watching
the call can see: dialing, ringing, answered, ended, finalized.
"""

from __future__ import annotations

from enum import Enum


class CallLifecycle(str, Enum):
    DIALING = "dialing"
    RINGING = "ringing"
    ANSWERED = "answered"
    ENDED = "ended"
    FINALIZED = "finalized"


_NEXT: dict[CallLifecycle, frozenset[CallLifecycle]] = {
    CallLifecycle.DIALING: frozenset(
        {CallLifecycle.RINGING, CallLifecycle.ANSWERED, CallLifecycle.ENDED}
    ),
    CallLifecycle.RINGING: frozenset({CallLifecycle.ANSWERED, CallLifecycle.ENDED}),
    CallLifecycle.ANSWERED: frozenset({CallLifecycle.ENDED}),
    CallLifecycle.ENDED: frozenset({CallLifecycle.FINALIZED}),
    CallLifecycle.FINALIZED: frozenset(),
}


def next_state(
    current: CallLifecycle | None,
    proposed: CallLifecycle,
) -> CallLifecycle | None:
    """Return the state to store, or None when the proposal changes nothing.

    The same state twice is a no-op. A jump that skips ringing is allowed,
    because some trunks report answer without a separate ring. A jump backward
    is ignored.
    """
    if current is None:
        if proposed is CallLifecycle.DIALING:
            return proposed
        return None
    if proposed is current:
        return None
    if proposed in _NEXT[current]:
        return proposed
    return None
