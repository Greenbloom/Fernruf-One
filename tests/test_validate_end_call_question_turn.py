from __future__ import annotations

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession


def test_end_call_not_blocked_when_question_was_previous_turn() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Ist die Verbindung gut verständlich?"},
            {"role": "user", "content": "Ja, alles super."},
            {
                "role": "assistant",
                "content": "Perfekt, dann ist die Verbindung in beide Richtungen sauber.",
            },
        ]
    )
    session.note_user_transcript("Ja, alles super.")
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call(
        "Test ok",
        settings,
        goodbye="Vielen Dank für den Test und auf Wiederhören.",
    )
    assert err is None


def test_coerce_goodbye_dict_to_empty_string() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Auf Wiederhören und vielen Dank!"},
            {"role": "user", "content": "Ja, auf Wiederhören."},
        ]
    )
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert session.validate_end_call("done", settings, goodbye={}) is None  # type: ignore[arg-type]
