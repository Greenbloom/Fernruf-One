"""Last boundary before TTS: tool-call syntax is never spoken."""

from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.tool_text import ToolTextGate, speakable_only

LOG = logging.getLogger("fernruf.voice.tool_text")


class ToolTextGuardProcessor(FrameProcessor):
    """Drop assistant text that looks like a tool call before it reaches TTS.

    The LLM service turns a textual ``end_call(...)`` into a real tool call.
    This processor is the backstop: whatever still looks like a tool call,
    JSON tool blob, ``<tool_call>`` block, or bare function name is removed.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._gate = ToolTextGate()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMFullResponseStartFrame):
            self._gate = ToolTextGate()

        if isinstance(frame, LLMTextFrame):
            speakable = self._gate.feed(frame.text or "")
            if not speakable:
                await super().process_frame(frame, direction)
                return
            if speakable != frame.text:
                replacement = LLMTextFrame(text=speakable)
                replacement.append_to_context = frame.append_to_context
                frame = replacement
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            tail = self._gate.finish()
            if tail.strip():
                held = LLMTextFrame(text=tail)
                held.append_to_context = False
                await self.push_frame(held, direction)
            if self._gate.calls:
                LOG.info("Dropped textual tool call before TTS")
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSSpeakFrame) and (frame.text or ""):
            speakable = speakable_only(frame.text)
            if not speakable:
                LOG.info("Dropping tool-call text before TTS (%s chars)", len(frame.text))
                await super().process_frame(frame, direction)
                return
            if speakable != frame.text.strip():
                LOG.info("Stripped tool-call syntax from speech (%s chars)", len(frame.text))
                frame = TTSSpeakFrame(
                    text=speakable,
                    append_to_context=frame.append_to_context,
                )

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
