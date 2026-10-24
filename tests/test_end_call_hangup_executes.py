from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
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


def test_end_call_guard_invokes_hangup_shutdown_and_end_frame() -> None:
    session = M2CallSession()
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=0.05,
    )
    hangup = AsyncMock()
    shutdown = AsyncMock()
    hooks = M2CallHooks(
        send_dtmf=AsyncMock(),
        hangup=hangup,
        session=session,
        request_pipeline_shutdown=shutdown,
    )
    guard = CallHangup(hooks=hooks, session=session, settings=settings)
    pushed: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        pushed.append(frame)

    guard.push_frame = capture  # type: ignore[method-assign]

    session.phase.begin_ending(
        reason="test",
        goodbye="Auf Wiederhören!",
        await_playback=True,
    )

    async def run() -> None:
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        goodbye = "Auf Wiederhören!"
        await guard.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=goodbye, text_type=TextType.SENTENCE, context_id="bye"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0
        assert shutdown.await_count == 0
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.08)
        assert hangup.await_count == 1
        assert shutdown.await_count == 1
        assert any(isinstance(f, EndFrame) for f in pushed)

    asyncio.run(run())
