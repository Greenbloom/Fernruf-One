from __future__ import annotations

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession


def test_empty_goodbye_allowed_when_user_says_farewell() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {
                "role": "assistant",
                "content": "Vielen Dank für Ihre Zeit und auf Wiederhören!",
            },
            {"role": "user", "content": "Auf Wiederhören."},
        ]
    )
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert (
        session.validate_end_call("Test ok", settings, goodbye="") is None
    )
