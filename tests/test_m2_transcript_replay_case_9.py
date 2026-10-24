from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools
from pipecat.frames.frames import BotStoppedSpeakingFrame, TextType, TTSStartedFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.call_hangup import CallHangup

SENTENCE_1 = "Hallo, ich rufe für einen kurzen Test an."
SENTENCE_2 = "Können Sie mich gut verstehen?"
FULL_COMPLETION = f"{SENTENCE_1} {SENTENCE_2}"


def test_replay_1441_blocks_end_call_from_full_llm_completion_with_question() -> None:
    session = M2CallSession()
    session.llm_completion_text = FULL_COMPLETION
    session.assistant_turn_text = SENTENCE_1
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call(
        "Test ok",
        settings,
        goodbye="Vielen Dank, auf Wiederhören.",
    )
    assert err is not None
    assert "question" in err.lower()


def test_replay_1441_end_call_tool_rejects_and_skips_goodbye_after_stream() -> None:
    session = M2CallSession()
    session.llm_completion_text = FULL_COMPLETION
    session.assistant_turn_text = SENTENCE_1
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    seen: list = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        seen.append(result)

    params = AsyncMock()
    params.context = None
    params.tool_call_id = "1441"
    params.result_callback = result_callback

    asyncio.run(
        end_call(
            params,
            reason="Test abgeschlossen",
            goodbye="Vielen Dank, auf Wiederhören.",
        )
    )
    assert seen[0]["ok"] is False
    assert session.pending_tool_tts == []
    assert session.pending_hangup is False


def test_replay_1441_hangup_fallback_waits_until_goodbye_tts_starts() -> None:
    session = M2CallSession()
    session.llm_completion_text = FULL_COMPLETION
    session.assistant_turn_text = SENTENCE_1
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=0.05,
        hangup_no_audio_fallback_s=0.35,
        min_call_duration_before_hangup_s=0.0,
    )
    hangup = AsyncMock()
    guard = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session),
        session=session,
        settings=settings,
    )
    guard.push_frame = AsyncMock()  # type: ignore[method-assign]
    goodbye = "Vielen Dank, auf Wiederhören."
    session.phase.begin_ending(reason="done", goodbye=goodbye, await_playback=True)

    async def run() -> None:
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(TTSStartedFrame(context_id="tail"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=SENTENCE_2, text_type=TextType.SENTENCE, context_id="tail"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0
        await guard.process_frame(
            TTSTextFrame(text=goodbye, text_type=TextType.SENTENCE, context_id="bye"),
            FrameDirection.DOWNSTREAM,
        )
        assert hangup.await_count == 0

    asyncio.run(run())
