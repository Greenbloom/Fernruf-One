from __future__ import annotations

import asyncio
from pathlib import Path

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSStoppedFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.frames.metrics_frames import (
    FishTtsFirstAudioFrame,
    LlmFirstTokenFrame,
    LlmInferenceStartedFrame,
)
from fernruf.voice.latency import LatencyLog
from fernruf.voice.processors.metrics import ConversationMetricsProcessor
from fernruf.voice.verify_orchestration import median_latency_report


def test_metrics_turn_chain_populates_medians(tmp_path: Path) -> None:
    log = LatencyLog(tmp_path / "lat.jsonl")
    session = M2CallSession()
    metrics = ConversationMetricsProcessor(
        call_id="t1",
        provider="openrouter",
        model="test",
        latency_log=log,
        session=session,
    )

    async def run() -> None:
        await metrics.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(
            TranscriptionFrame(text="Ja.", user_id="u", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        await metrics.process_frame(LlmInferenceStartedFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LlmFirstTokenFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMTextFrame(text="Hallo!"), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(FishTtsFirstAudioFrame(ttfa_ms=12.0), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    report = median_latency_report(log)
    assert report["turns"] == 1.0
    assert report["stt_final_ms_median"] is not None
    assert report["llm_first_token_ms_median"] is not None
    assert report["fish_ttfa_ms_median"] is not None
    assert report["fish_ttfa_ms_median"] >= 0


def test_metrics_three_turns_non_negative_medians(tmp_path: Path) -> None:
    log = LatencyLog(tmp_path / "lat3.jsonl")
    session = M2CallSession()
    metrics = ConversationMetricsProcessor(
        call_id="t3",
        provider="openrouter",
        model="test",
        latency_log=log,
        session=session,
    )

    async def one_turn(text: str) -> None:
        await metrics.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(
            TranscriptionFrame(text=text, user_id="u", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        await metrics.process_frame(LlmInferenceStartedFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LlmFirstTokenFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMTextFrame(text="Ok."), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(FishTtsFirstAudioFrame(ttfa_ms=5.0), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await metrics.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    async def run() -> None:
        for t in ("Eins.", "Zwei.", "Drei."):
            await one_turn(t)
            await asyncio.sleep(0.01)

    asyncio.run(run())
    report = median_latency_report(log)
    assert report["turns"] == 3.0
    assert report["llm_first_token_ms_median"] is not None
    assert report["fish_ttfa_ms_median"] is not None
    assert report["fish_ttfa_ms_median"] >= 0
