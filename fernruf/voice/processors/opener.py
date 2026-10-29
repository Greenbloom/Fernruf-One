from __future__ import annotations

import logging

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InputTransportMessageFrame,
    InterruptionFrame,
    LLMMessagesAppendFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession

LOG = logging.getLogger("fernruf.voice.opener")


class DisclosureOpenerProcessor(FrameProcessor):
    """Queue AI disclosure via TTSSpeakFrame on MEDIA_START; block barge-in until done."""

    def __init__(
        self,
        *,
        disclosure: str,
        privacy_line: str = "",
        request: str = "",
        session: M2CallSession | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._disclosure = disclosure
        self._request = (request or "").strip()
        self._spoken = disclosure if not self._request else f"{disclosure} {self._request}"
        self._privacy_line = (privacy_line or "").strip()
        self._session = session
        self._queued = False
        self._protect = True
        self._context_logged = False
        self._segments_left = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if (
            isinstance(frame, InputTransportMessageFrame)
            and frame.message.get("event") == "MEDIA_START"
            and not self._queued
        ):
            self._queued = True
            self._segments_left = 1 + (1 if self._privacy_line else 0)
            LOG.info("Queuing AI disclosure opener (%s chars)", len(self._spoken))
            await self.push_frame(
                TTSSpeakFrame(text=self._spoken, append_to_context=False),
                direction,
            )
            if self._privacy_line:
                await self.push_frame(
                    TTSSpeakFrame(text=self._privacy_line, append_to_context=False),
                    direction,
                )
            if not self._context_logged:
                self._context_logged = True
                if self._session is not None:
                    self._session.note_transcript_line("agent", self._spoken)
                    if self._privacy_line:
                        self._session.note_transcript_line("agent", self._privacy_line)
                await self.push_frame(
                    LLMMessagesAppendFrame(
                        messages=[{"role": "assistant", "content": self._spoken}],
                        run_llm=False,
                    ),
                    direction,
                )

        if self._protect and isinstance(frame, InterruptionFrame):
            LOG.debug("Ignoring barge-in during AI disclosure")
            return

        if self._protect and isinstance(frame, BotStoppedSpeakingFrame) and self._queued:
            self._segments_left = max(0, self._segments_left - 1)
            if self._segments_left <= 0:
                self._protect = False
                LOG.info("AI disclosure playback finished")

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
