from __future__ import annotations

import asyncio

from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.spoken_assistant_context import SpokenAssistantContextProcessor


def test_llm_text_not_appended_to_assistant_context() -> None:
    proc = SpokenAssistantContextProcessor()
    out: list[LLMTextFrame] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        if isinstance(frame, LLMTextFrame):
            out.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMTextFrame(text="Hallo! Alles klar?"), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    assert len(out) == 1
    assert out[0].append_to_context is False
