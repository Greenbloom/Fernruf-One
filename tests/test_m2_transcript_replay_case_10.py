from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.processors.call_hangup import CallHangup
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TextType,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

GOODBYE = "Auf Wiederhören."
TAIL = "Der Test war erfolgreich."


def test_replay_1447_goodbye_tts_text_arms_hangup_not_prior_segment() -> None:
    """Replay 14:47:31 goodbye TTS — hangup ~margin after playout, not after apology."""
    session = M2CallSession()
    session.phase.begin_ending(reason="Test ok", goodbye=GOODBYE, await_playback=True)
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
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=TAIL, text_type=TextType.SENTENCE, context_id="seg-test"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert hangup.await_count == 0

        await guard.process_frame(TTSStartedFrame(context_id="seg-goodbye"), FrameDirection.DOWNSTREAM)
        await guard.process_frame(
            TTSTextFrame(text=GOODBYE, text_type=TextType.SENTENCE, context_id="seg-goodbye"),
            FrameDirection.DOWNSTREAM,
        )
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.02)
        assert hangup.await_count == 0
        await asyncio.sleep(0.06)
        assert hangup.await_count == 1

    asyncio.run(run())


def test_replay_1447_metrics_two_completed_turns(tmp_path) -> None:
    from fernruf.voice.latency import LatencyLog
    from fernruf.voice.processors.metrics import ConversationMetricsProcessor
    from fernruf.voice.verify_orchestration import median_latency_report
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TTSStoppedFrame,
        UserStoppedSpeakingFrame,
    )
    from fernruf.voice.frames.metrics_frames import LlmFirstTokenFrame, LlmInferenceStartedFrame

    log = LatencyLog(tmp_path / "1447.jsonl")
    session = M2CallSession()
    metrics = ConversationMetricsProcessor(
        call_id="1447",
        provider="openrouter",
        model="test",
        latency_log=log,
        session=session,
    )

    async def bot_turn(user_text: str) -> None:
        await metrics.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(
            TranscriptionFrame(text=user_text, user_id="u", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        await metrics.process_frame(LlmInferenceStartedFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LlmFirstTokenFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMTextFrame(text="Ok."), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    async def run() -> None:
        await bot_turn("Ja.")
        await bot_turn("Verstanden.")

    asyncio.run(run())
    report = median_latency_report(log)
    assert report["turns"] == 2.0
