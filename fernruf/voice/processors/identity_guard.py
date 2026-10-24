from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, LLMTextFrame, TTSSpeakFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.identity import scrub_identity_text

LOG = logging.getLogger("fernruf.voice.identity")


class IdentityOutputGuardProcessor(FrameProcessor):
    """Block or rewrite assistant speech that claims to be the principal or a human before TTS."""

    def __init__(self, *, principal: str, replacement: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._principal = principal
        self._replacement = replacement

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, (LLMTextFrame, TTSTextFrame)):
            cleaned, changed = scrub_identity_text(
                frame.text, principal=self._principal, replacement=self._replacement
            )
            if changed:
                LOG.warning(
                    "identity guard: rewrote assistant text (%s chars -> %s chars)",
                    len(frame.text),
                    len(cleaned),
                )
                frame = type(frame)(text=cleaned)
        elif isinstance(frame, TTSSpeakFrame) and frame.text:
            cleaned, changed = scrub_identity_text(
                frame.text, principal=self._principal, replacement=self._replacement
            )
            if changed:
                LOG.warning("identity guard: rewrote TTSSpeakFrame before playback")
                frame = TTSSpeakFrame(
                    text=cleaned,
                    append_to_context=frame.append_to_context,
                )

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
