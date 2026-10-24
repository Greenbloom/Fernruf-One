from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TextType,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.processors.call_hangup import CallHangup


def test_hangup_waits_for_bot_stopped_plus_margin() -> None:
    session = M2CallSession()
    session.user_turn_count = 5
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=0.25,
        hangup_playback_safety_timeout_s=10.0,
    )
    hangup = AsyncMock()
    guard = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session),
        session=session,
        settings=settings,
    )
    guard.push_frame = AsyncMock()  # type: ignore[method-assign]

    session.phase.begin_ending(
        reason="done",
        goodbye="Alles klar, danke dir! Auf Wiederhören!",
        await_playback=True,
    )

    async def run() -> None:
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        goodbye = "Alles klar, danke dir! Auf Wiederhören!"
        await guard.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=goodbye, text_type=TextType.SENTENCE, context_id="bye"),
            FrameDirection.DOWNSTREAM,
        )
        t_stop = time.monotonic()
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        assert hangup.await_count == 0
        await asyncio.sleep(0.25)
        assert hangup.await_count == 1
        assert time.monotonic() - t_stop >= 0.2

    asyncio.run(run())
