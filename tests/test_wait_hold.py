"""A wait request is not an answer, and it is not permission to hang up."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools

WAIT = "Warte, ich teste eben die Knöpfe oben rechts."
ASKED = "Hat sich das Fenster von selbst geöffnet, und funktionieren die Knöpfe oben rechts?"


def _settings() -> Settings:
    return Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)


def _session_after_wait() -> M2CallSession:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": ASKED},
            {"role": "user", "content": WAIT},
        ]
    )
    session.assistant_turn_text = "Verstehe, ich warte kurz."
    session.llm_completion_text = "Verstehe, ich warte kurz."
    return session


def test_replay_wait_line_blocks_end_call_in_the_same_turn() -> None:
    """The 2026-11-12 turn: the model agreed to wait and called end_call."""
    session = _session_after_wait()
    settings = _settings()
    err = session.validate_end_call(
        "Testanruf abgeschlossen",
        settings,
        goodbye="Auf Wiederhören.",
    )
    assert err is not None
    assert "wait" in err.lower()
    assert not session.phase.is_ending

    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    seen: list[dict] = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        del properties
        seen.append(result)

    params = AsyncMock()
    params.context = None
    params.tool_call_id = "wait-turn"
    params.result_callback = result_callback
    asyncio.run(
        end_call(
            params,
            reason="Testanruf abgeschlossen",
            goodbye="Auf Wiederhören.",
        )
    )
    assert seen[0]["ok"] is False
    assert hooks.hangup.await_count == 0
    assert not session.phase.is_ending


def test_hold_phrases_are_not_answers() -> None:
    settings = _settings()
    phrases = (
        "Warte",
        "Moment",
        "einen Augenblick",
        "hold on",
        "wait",
        "Bitte warten Sie kurz.",
    )
    for phrase in phrases:
        session = M2CallSession()
        session.sync_from_llm_messages(
            [
                {"role": "assistant", "content": ASKED},
                {"role": "user", "content": phrase},
            ]
        )
        err = session.validate_end_call("done", settings, goodbye="Auf Wiederhören.")
        assert err is not None, phrase
        assert "wait" in err.lower(), phrase
        assert not session.phase.is_ending


def test_agreeing_to_wait_blocks_end_call_even_without_a_hold_phrase() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": ASKED},
            {"role": "user", "content": "Die Knöpfe sind oben rechts."},
        ]
    )
    session.assistant_turn_text = "Verstehe, ich warte kurz."
    session.llm_completion_text = "Verstehe, ich warte kurz."
    err = session.validate_end_call(
        "Testanruf abgeschlossen",
        _settings(),
        goodbye="Auf Wiederhören.",
    )
    assert err is not None
    assert "wait" in err.lower()
    assert not session.phase.is_ending


def test_a_real_answer_can_still_end() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": ASKED},
            {
                "role": "user",
                "content": "Ja, das Fenster hat sich geöffnet und die Knöpfe funktionieren.",
            },
        ]
    )
    assert (
        session.validate_end_call(
            "Testanruf abgeschlossen",
            _settings(),
            goodbye="Auf Wiederhören.",
        )
        is None
    )


def test_nicht_auflegen_is_not_a_request_to_hang_up() -> None:
    session = M2CallSession()
    session.note_user_transcript("Na warte, du kannst das noch nicht auflegen.")
    assert session.phase.callee_said_goodbye is False
    err = session.validate_end_call("done", _settings(), goodbye="Auf Wiederhören.")
    assert err is not None
    assert not session.phase.is_ending

    allowed = M2CallSession()
    allowed.note_user_transcript("Wir können auflegen, danke.")
    assert allowed.phase.callee_said_goodbye is True
