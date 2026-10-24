from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from pipecat.frames.frames import BotStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.processors.call_hangup import CallHangup


def test_empty_goodbye_hangup_without_bot_speaking() -> None:
    session = M2CallSession()
    session.note_user_transcript("Auf Wiederhören.")
    session.commit_assistant_turn("Vielen Dank für Ihre Zeit und auf Wiederhören!")
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

    async def run() -> None:
        t0 = time.monotonic()
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.08)
        assert hangup.await_count == 1
        assert time.monotonic() - t0 < 1.0

    asyncio.run(run())
