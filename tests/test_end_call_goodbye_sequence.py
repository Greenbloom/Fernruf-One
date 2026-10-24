from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TextType,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.processors.call_hangup import CallHangup
from fernruf.voice.processors.filler import LlmFillerProcessor


def test_Alex_transcript_end_call_without_second_goodbye_hangs_up() -> None:
    """Replay deadlock scenario: mutual goodbye, end_call(reason) only, must hang up."""
    session = M2CallSession()
    session.user_turn_count = 4
    session.call_started_at = time.monotonic() - 120
    session.commit_assistant_turn(
        "Ich wollte nur kurz testen... Vielen Dank und auf Wiederhören!"
    )
    session.note_user_transcript("Okay.")
    session.note_user_transcript("Auf Wiederhören.")

    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
        hangup_after_bot_stopped_margin_s=0.05,
    )
    assert session.validate_end_call(
        "Der Gesprächspartner hat sich verabschiedet.",
        settings,
        goodbye="",
    ) is None

    hangup = AsyncMock()
    guard = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session),
        session=session,
        settings=settings,
    )
    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        pushed.append(frame)

    guard.push_frame = capture  # type: ignore[method-assign]

    session.phase.begin_ending(
        reason="Der Gesprächspartner hat sich verabschiedet.",
        await_playback=False,
    )

    async def run() -> None:
        t0 = time.monotonic()
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.35)
        assert hangup.await_count == 1
        assert time.monotonic() - t0 < 5.0
        assert session.hung_up

    asyncio.run(run())


def test_end_call_goodbye_string_spoken_then_hangup_within_5s() -> None:
    session = M2CallSession()
    session.user_turn_count = 5
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=3,
        min_call_duration_before_hangup_s=0.0,
        hangup_after_bot_stopped_margin_s=0.05,
    )
    hangup = AsyncMock()
    guard = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session),
        session=session,
        settings=settings,
    )
    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        pushed.append((direction, frame))

    guard.push_frame = capture  # type: ignore[method-assign]

    session.phase.begin_ending(
        reason="done",
        goodbye="Vielen Dank, auf Wiederhören!",
        await_playback=True,
    )

    async def run() -> None:
        t0 = time.monotonic()
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        speaks = [(d, f) for d, f in pushed if isinstance(f, TTSSpeakFrame)]
        assert len(speaks) == 1
        direction, speak = speaks[0]
        assert direction == FrameDirection.UPSTREAM
        assert speak.append_to_context is False
        assert "Wiederhören" in speak.text

        goodbye = "Vielen Dank, auf Wiederhören!"
        await guard.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=goodbye, text_type=TextType.SENTENCE, context_id="bye"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.35)

        assert hangup.await_count == 1
        assert time.monotonic() - t0 < 5.0

    asyncio.run(run())


def test_no_filler_while_call_is_ending() -> None:
    session = M2CallSession()
    session.note_user_transcript("Auf Wiederhören.")
    session.phase.begin_ending(reason="farewell", await_playback=True)
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.01, session=session)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        from pipecat.frames.frames import TranscriptionFrame

        from pipecat.frames.frames import LLMFullResponseStartFrame

        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert not any(getattr(f, "text", None) == "Einen Moment…" for f in pushed)
