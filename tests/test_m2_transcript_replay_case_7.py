from __future__ import annotations

import time

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.processors.merge_user_context import merge_consecutive_assistant_messages


def test_replay_1355_answered_task_bypasses_min_call_duration() -> None:
    session = M2CallSession()
    session.call_started_at = time.monotonic() - 28.0
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Ist das so klar für Sie?"},
            {"role": "user", "content": "Ja, das ist klar."},
            {"role": "assistant", "content": "Perfekt, danke für die Rückmeldung."},
        ]
    )
    settings = Settings(_env_file=None)
    assert settings.min_call_duration_before_hangup_s == 45.0
    assert (
        session.validate_end_call(
            "Aufgabe erledigt, Gegenstelle hat bestätigt",
            settings,
            goodbye="Vielen Dank und auf Wiederhören.",
        )
        is None
    )


def test_replay_1355_dedupe_duplicate_assistant_context() -> None:
    duplicate = "Perfekt, danke für die Rückmeldung."
    messages = [
        {"role": "user", "content": "Ja, das ist klar."},
        {"role": "assistant", "content": duplicate},
        {"role": "assistant", "content": duplicate},
    ]
    merged = merge_consecutive_assistant_messages(messages)
    assert len(merged) == 2
    assert merged[-1]["content"] == duplicate
