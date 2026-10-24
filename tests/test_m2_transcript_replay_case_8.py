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


def test_replay_1420_blocks_end_call_with_question_in_same_response() -> None:
    session = M2CallSession()
    session.assistant_turn_text = "Hören Sie mich gut?"
    session.sync_from_llm_messages(
        [{"role": "assistant", "content": "Guten Tag, ich rufe im Auftrag von Alex Example an."}]
    )
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    err = session.validate_end_call(
        "Test",
        settings,
        goodbye="Vielen Dank, auf Wiederhören.",
    )
    assert err is not None
    assert "question" in err.lower()


def test_replay_1420_goodbye_hangup_waits_for_goodbye_tts_start() -> None:
    session = M2CallSession()
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=0.05,
        hangup_no_audio_fallback_s=2.0,
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
        await guard.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text="Hören Sie mich gut?", text_type=TextType.SENTENCE, context_id="q"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.02)
        assert hangup.await_count == 0
        await guard.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=goodbye, text_type=TextType.SENTENCE, context_id="bye"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.08)
        assert hangup.await_count == 1

    asyncio.run(run())


def test_replay_1420_blocked_end_call_does_not_queue_goodbye() -> None:
    session = M2CallSession()
    session.assistant_turn_text = "Hören Sie mich gut?"
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    seen: list = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        seen.append(result)

    params = AsyncMock()
    params.context = None
    params.tool_call_id = "1420"
    params.result_callback = result_callback

    asyncio.run(
        end_call(
            params,
            reason="done",
            goodbye="Vielen Dank, auf Wiederhören.",
        )
    )
    assert seen[0]["ok"] is False
    assert session.pending_tool_tts == []
