"""Stop a demo call by the same goodbye path as end_call."""

from __future__ import annotations

from fernruf.voice.hooks import M2CallHooks


async def end_demo_politely(session, hooks: M2CallHooks, goodbye: str) -> bool:
    """Arm the spoken goodbye, then hang up.

    This is the end_call path: the phase flips to ending, CallHangup speaks
    ``goodbye``, and the existing hangup waits until that audio has played.
    Returns False when the pipeline has not registered the hangup yet.
    """
    phase = session.phase
    if phase.is_hung_up:
        return True
    if not phase.is_ending:
        phase.begin_ending(
            reason="demo time limit",
            goodbye=(goodbye or "").strip(),
        )
    schedule = hooks.schedule_hangup
    if schedule is None:
        return False
    await schedule()
    return True
