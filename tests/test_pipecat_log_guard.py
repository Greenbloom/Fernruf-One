from __future__ import annotations

from fernruf.voice.pipecat_log_guard import PipecatLogGuard


def test_log_guard_remove_is_idempotent() -> None:
    guard = PipecatLogGuard()
    guard.install()
    guard.remove()
    guard.remove()
    guard.install()
    guard.remove()
