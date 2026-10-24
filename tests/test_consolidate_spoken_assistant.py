from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TextType,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.consolidate_spoken_assistant import (
    ConsolidateSpokenAssistantProcessor,
)


def test_consolidates_all_spoken_clauses_into_one_context_frame() -> None:
    proc = ConsolidateSpokenAssistantProcessor()
    emitted: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        emitted.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSTextFrame("Hallo! Schön, dass du dran bist.", TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSTextFrame(
                "Dies ist ein kurzer Testanruf – kannst du mich gut verstehen?",
                TextType.SENTENCE,
            ),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    append = [f for f in emitted if isinstance(f, LLMMessagesAppendFrame)]
    assert len(append) == 1
    msg = append[0].messages[0]
    assert msg["role"] == "assistant"
    assert "verstehen?" in msg["content"]
    assert not any(isinstance(f, TTSTextFrame) for f in emitted)


def test_emits_after_tts_when_response_end_arrives_first() -> None:
    proc = ConsolidateSpokenAssistantProcessor()
    emitted: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        emitted.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSTextFrame("Hallo!", TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSTextFrame("Noch eine Frage?", TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    append = [f for f in emitted if isinstance(f, LLMMessagesAppendFrame)]
    assert len(append) == 1
    assert "Frage?" in append[0].messages[0]["content"]
    assert "Hallo!" in append[0].messages[0]["content"]


def test_interruption_flushes_partial_spoken_context() -> None:
    proc = ConsolidateSpokenAssistantProcessor()
    emitted: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        emitted.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSTextFrame("Erster Satz.", TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSTextFrame("Zweiter Satz.", TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        from pipecat.frames.frames import InterruptionFrame

        await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    append = [f for f in emitted if isinstance(f, LLMMessagesAppendFrame)]
    assert len(append) == 1
    assert "Erster Satz." in append[0].messages[0]["content"]
    assert "Zweiter Satz." in append[0].messages[0]["content"]
