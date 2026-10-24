from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

LOG = logging.getLogger("fernruf.voice.tts_coverage")


class TtsCoverageProcessor(FrameProcessor):
    """Record assistant LLM text that reaches the TTS stage (verify / tests)."""

    def __init__(self, *, synthesized: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._synthesized = synthesized

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMTextFrame) and (frame.text or "").strip():
            snippet = frame.text.strip()
            self._synthesized.append(snippet)
            LOG.debug("LLM text toward TTS: %s", snippet[:80])
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
