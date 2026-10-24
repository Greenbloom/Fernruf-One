from __future__ import annotations

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession


def test_end_call_blocked_without_goodbye_or_exchange() -> None:
    session = M2CallSession()
    session.user_turn_count = 5
    settings = Settings(_env_file=None, min_user_turns_before_hangup=3)
    err = session.validate_end_call("done", settings, goodbye="")
    assert err is not None
    assert "goodbye" in err.lower()


def test_end_call_allowed_with_goodbye_argument() -> None:
    session = M2CallSession()
    session.note_user_transcript("Ja, das passt.")
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
    )
    assert session.validate_end_call("done", settings, goodbye="Vielen Dank, auf Wiederhören!") is None


def test_end_call_blocked_when_question_unanswered() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Hören Sie mich gut?"},
        ]
    )
    session.assistant_turn_text = "Alles klar, danke."
    settings = Settings(
        _env_file=None,
        min_call_duration_before_hangup_s=0.0,
    )
    err = session.validate_end_call("test done", settings, goodbye="Tschüss!")
    assert err is not None
    assert "answer" in err.lower()


def test_end_call_allowed_after_callee_answered_question() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Können Sie mich gut verstehen?"},
            {"role": "user", "content": "Ja, ich höre echt gut."},
        ]
    )
    session.assistant_turn_text = "Alles klar, danke dir!"
    settings = Settings(
        _env_file=None,
        min_call_duration_before_hangup_s=0.0,
    )
    assert session.validate_end_call(
        "Test ok",
        settings,
        goodbye="Auf Wiederhören!",
    ) is None


def test_end_call_allowed_after_mutual_goodbye_without_goodbye_arg() -> None:
    session = M2CallSession()
    session.commit_assistant_turn(
        "Ich wollte nur kurz testen... Vielen Dank und auf Wiederhören!"
    )
    session.note_user_transcript("Okay.")
    session.note_user_transcript("Auf Wiederhören.")
    session.user_turn_count = 4
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
    )
    assert session.callee_goodbye_seen
    assert session.validate_end_call("partner said goodbye", settings, goodbye="") is None


def test_callee_goodbye_ciao_bypasses_min_turns() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages([{"role": "user", "content": "Ciao."}])
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=10,
        min_call_duration_before_hangup_s=999.0,
    )
    assert session.callee_goodbye_seen
    assert session.validate_end_call(
        "callee said ciao",
        settings,
        goodbye="Auf Wiederhören!",
    ) is None


def test_empty_goodbye_allowed_after_mutual_ciao_and_tschuess() -> None:
    session = M2CallSession()
    session.note_user_transcript("Super, ciao.")
    session.assistant_turn_text = "Tschüss! Schönen Tag noch."
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
    )
    assert session.validate_end_call("farewell", settings, goodbye="") is None


def test_end_call_blocked_when_assistant_asked_question() -> None:
    session = M2CallSession()
    session.user_turn_count = 5
    session.assistant_turn_text = "Können Sie mich gut verstehen?"
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=0,
        min_call_duration_before_hangup_s=0.0,
    )
    err = session.validate_end_call("done", settings, goodbye="Tschüss!")
    assert err is not None
    assert "question" in err.lower()


def test_misheard_bye_after_opener_not_goodbye() -> None:
    session = M2CallSession()
    session.commit_assistant_turn("Guten Tag, Testanruf.")
    session.note_user_transcript("Bye.")
    assert not session.callee_goodbye_seen


def test_english_spoken_goodbye_does_not_queue_a_second_line() -> None:
    session = M2CallSession()
    session.phase.call_language = "en"
    session.phase.goodbye_phrase = "Goodbye."
    session.note_user_transcript("Yes, Tuesday works.")
    session.phase.note_assistant_completion("Thank you. Goodbye.")
    assert session.phase.is_ending
    assert session.phase.goodbye_tts == ""
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert session.validate_end_call("done", settings, goodbye="Thank you. Goodbye.") is None
    assert session.phase.goodbye_tts == ""


def test_good_afternoon_is_not_a_farewell() -> None:
    session = M2CallSession()
    session.phase.call_language = "en"
    session.phase.goodbye_phrase = "Goodbye."
    session.note_user_transcript("Hello?")
    session.phase.note_assistant_completion("Good afternoon. Does Tuesday still work?")
    assert not session.phase.is_ending
    assert session.phase.awaiting_answer
    session.note_user_transcript("Good afternoon.")
    assert not session.callee_goodbye_seen


def test_unclear_transcript_blocks_end_call_once() -> None:
    session = M2CallSession()
    session.phase.call_language = "en"
    session.phase.goodbye_phrase = "Goodbye."
    session.phase.awaiting_answer = True
    session.note_user_transcript("Du hast eine Chemie.")
    session.assistant_turn_text = "Thank you. Goodbye."
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call("done", settings, goodbye="Thank you. Goodbye.")
    assert err is not None
    assert "unclear" in err
    assert session.phase.reask_used
    assert not session.phase.is_ending
    session.phase.reask_spoken = True
    session.note_user_transcript("Yes, Tuesday at three.")
    session.assistant_turn_text = "Thank you."
    session.llm_completion_text = ""
    assert session.validate_end_call("done", settings, goodbye="Goodbye.") is None


def test_callee_requested_end_bypasses_min_turns() -> None:
    session = M2CallSession()
    session.note_user_transcript("Wir können auflegen, danke.")
    session.commit_assistant_turn("Gerne, auf Wiederhören!")
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=10,
        min_call_duration_before_hangup_s=999.0,
    )
    assert session.callee_requested_end
    assert session.validate_end_call("callee ended", settings, goodbye="") is None
