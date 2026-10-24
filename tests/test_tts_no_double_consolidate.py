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


def test_consolidate_emits_context_append_not_tts_text() -> None:
    proc = ConsolidateSpokenAssistantProcessor()
    emitted: list = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        emitted.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        for sent in ("Satz eins.", "Satz zwei."):
            await proc.process_frame(
                TTSTextFrame(sent, TextType.SENTENCE),
                FrameDirection.DOWNSTREAM,
            )
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    assert any(isinstance(f, LLMMessagesAppendFrame) for f in emitted)
    assert not any(
        isinstance(f, TTSTextFrame) and f.append_to_context for f in emitted
    )
