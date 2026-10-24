"""Synthetic scenario: the goal was never spoken, then the call ended on thanks."""

from __future__ import annotations

from fernruf.settings import Settings
from fernruf.voice.call_phase import answer_is_unclear
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.must_say import goal_needs_answer, supplement_must_say

MUST = "Alex wanted to know how your day has been."
GOAL = "ask the callee how their day has been, then end warmly"


def _session() -> M2CallSession:
    session = M2CallSession()
    session.phase.must_say = (MUST,)
    session.phase.goal_needs_answer = goal_needs_answer(GOAL)
    session.phase.call_language = "en"
    return session


def test_vielen_dank_is_not_a_goodbye_or_a_refusal() -> None:
    session = _session()
    session.note_user_transcript("You want.")
    session.note_user_transcript("Vielen Dank.")
    session.note_user_transcript("Vielen Dank. Vielen Dank.")
    assert session.callee_goodbye_seen is False
    assert session.phase.callee_refused is False
    assert answer_is_unclear("Vielen Dank.", call_language="en") is True
    assert answer_is_unclear("Thanks for watching.", call_language="en") is True
    assert goal_needs_answer(GOAL) is True


def test_end_call_blocked_until_must_say_is_spoken() -> None:
    session = _session()
    session.note_user_transcript("You want.")
    session.note_user_transcript("Vielen Dank.")
    session.append_llm_completion("Guten Abend.")
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call("wrapping up", settings, goodbye="Goodbye.")
    assert err is not None
    assert "must_say" in err
    assert session.phase.is_ending is False


def test_first_turn_appends_missing_must_say_and_waits() -> None:
    session = _session()
    session.note_user_transcript("You want.")
    session.llm_turns = 1
    session.append_llm_completion("Guten Abend.")
    updated = supplement_must_say(session.llm_completion_text, session.phase.must_say)
    assert updated == f"Guten Abend. {MUST}"
    session.llm_completion_text = updated
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call("done", settings, goodbye="Goodbye.")
    assert err is not None
    assert "question" in err.lower() or "answer" in err.lower()
    assert MUST in session.phase.agent_spoken
    assert session.phase.is_ending is False


def test_clear_hangup_may_end_before_must_say() -> None:
    session = _session()
    session.note_user_transcript("Auf Wiederhören.")
    assert session.callee_goodbye_seen is True
    session.append_llm_completion("Guten Abend.")
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert session.validate_end_call("callee hung up", settings, goodbye="") is None


def test_clear_refusal_may_end_before_must_say() -> None:
    session = _session()
    session.note_user_transcript("I'm not interested, please stop calling.")
    assert session.phase.callee_refused is True
    assert session.callee_goodbye_seen is False
    session.append_llm_completion("Guten Abend.")
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert session.validate_end_call("refused", settings, goodbye="") is None
