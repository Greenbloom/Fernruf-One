from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.filler import LlmFillerProcessor


def test_filler_starts_after_llm_request_not_on_stt_final() -> None:
    """Filler timer must not start on STT final while the user turn is still open."""
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.05)
    pushed: list = []

    async def capture(frame, direction) -> None:
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(
            TranscriptionFrame(text="Ja, alles funktioniert.", user_id="u1", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.12)

    asyncio.run(run())
    assert not any(getattr(f, "text", None) == "Einen Moment…" for f in pushed)


def test_filler_plays_when_llm_slow_after_turn() -> None:
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.05)
    pushed: list = []

    async def capture(frame, direction) -> None:
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.12)

    asyncio.run(run())
    assert any(getattr(f, "text", None) == "Einen Moment…" for f in pushed)


def test_filler_cancelled_when_user_starts_speaking_again() -> None:
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.2)
    pushed: list = []

    async def capture(frame, direction) -> None:
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        await filler.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.25)

    asyncio.run(run())
    assert not any(getattr(f, "text", None) == "Einen Moment…" for f in pushed)


def test_filler_cancelled_on_first_llm_text() -> None:
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.2)
    pushed: list = []

    async def capture(frame, direction) -> None:
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        await filler.process_frame(LLMTextFrame(text="Antwort."), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.25)

    asyncio.run(run())
    assert not any(getattr(f, "text", None) == "Einen Moment…" for f in pushed)


def test_filler_not_appended_to_context() -> None:
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.01)
    pushed: list = []

    async def capture(frame, direction) -> None:
        pushed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    speak = [f for f in pushed if getattr(f, "text", None) == "Einen Moment…"]
    assert len(speak) == 1
    assert speak[0].append_to_context is False


def test_filler_then_llm_both_pass_through() -> None:
    filler = LlmFillerProcessor(phrase="Einen Moment…", delay_s=0.05)
    passed: list = []

    async def capture(frame, direction) -> None:
        passed.append(frame)

    filler.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await filler.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.07)
        await filler.process_frame(
            LLMTextFrame(text="Ja, ich höre Sie."),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    llm_texts = [f.text for f in passed if isinstance(f, LLMTextFrame)]
    assert llm_texts == ["Ja, ich höre Sie."]
