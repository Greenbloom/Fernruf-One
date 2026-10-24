from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession

LOG = logging.getLogger("fernruf.voice.coalesce")


class CoalesceUserTurnGateProcessor(FrameProcessor):
    """Drop user-stop events while the LLM is in flight (merged after LLM ends)."""

    def __init__(self, *, session: M2CallSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session
        self._pending_transcript = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._pending_transcript = frame.text.strip()

        if isinstance(frame, UserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            text = self._pending_transcript
            self._pending_transcript = ""
            if text and self._session.llm_in_flight:
                self._session.pending_user_merge.append(text)
                LOG.info(
                    "Coalescing user turn while LLM in flight (%s part(s))",
                    len(self._session.pending_user_merge),
                )
                return

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class CoalesceUserTurnFlushProcessor(FrameProcessor):
    """After LLM finishes, inject one merged user message for queued partial turns."""

    def __init__(self, *, session: M2CallSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMFullResponseEndFrame):
            if self._session.phase.suppresses_llm:
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)
                return
            if self._session.pending_user_merge:
                merged = " ".join(self._session.pending_user_merge).strip()
                self._session.pending_user_merge.clear()
                if merged:
                    LOG.info("Flushing coalesced user message (%s chars)", len(merged))
                    await self.push_frame(
                        LLMMessagesAppendFrame(
                            messages=[{"role": "user", "content": merged}],
                            run_llm=True,
                        ),
                        FrameDirection.UPSTREAM,
                    )

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
