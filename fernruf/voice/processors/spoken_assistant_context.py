from __future__ import annotations

from pipecat.frames.frames import Frame, LLMTextFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class SpokenAssistantContextProcessor(FrameProcessor):
    """Keep assistant LLM context aligned with TTS output (spoken text only)."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, (LLMTextFrame, TTSTextFrame)):
            frame.append_to_context = False

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
