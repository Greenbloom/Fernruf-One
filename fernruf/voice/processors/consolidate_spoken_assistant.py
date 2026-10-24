from __future__ import annotations

import logging

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession

LOG = logging.getLogger("fernruf.voice.spoken_consolidate")


class ConsolidateSpokenAssistantProcessor(FrameProcessor):
    """Append one assistant context message per spoken turn (never re-feed TTS)."""

    def __init__(self, *, session: M2CallSession | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._collecting = False
        self._response_complete = False
        self._parts: list[str] = []

    def _reset(self) -> None:
        self._collecting = False
        self._response_complete = False
        self._parts = []

    @staticmethod
    def _join_parts(parts: list[str]) -> str:
        out = ""
        for part in parts:
            p = part.strip()
            if not p:
                continue
            if not out:
                out = p
                continue
            if out.endswith(("-", "–")):
                out = out.rstrip("-–") + p.lstrip()
            elif p[0] in ",.;:!?":
                out += p
            else:
                out = f"{out} {p}"
        return out.strip()

    async def _append_to_context(self, direction: FrameDirection, *, force: bool = False) -> None:
        if not self._parts:
            return
        if not force and not self._response_complete:
            return
        combined = self._join_parts(self._parts)
        self._parts = []
        self._response_complete = False
        self._collecting = False
        if not combined:
            return
        if self._session is not None:
            self._session.note_transcript_line("agent", combined)
        LOG.debug("Consolidated spoken assistant text (%s chars)", len(combined))
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "assistant", "content": combined}],
                run_llm=False,
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterruptionFrame):
            await self._append_to_context(direction, force=True)
            self._reset()
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._reset()
            self._collecting = True

        if isinstance(frame, TTSTextFrame) and self._collecting and frame.append_to_context:
            text = (frame.text or "").strip()
            if text and (not self._parts or self._parts[-1] != text):
                self._parts.append(frame.text)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            self._response_complete = True

        if isinstance(frame, BotStoppedSpeakingFrame):
            await self._append_to_context(direction)

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
