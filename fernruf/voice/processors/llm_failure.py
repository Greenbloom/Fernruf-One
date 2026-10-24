from __future__ import annotations

import logging

from pipecat.frames.frames import (
    EndFrame,
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.language import phrase
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks

LOG = logging.getLogger("fernruf.voice.llm_failure")

_APOLOGY = (
    "Entschuldigung, ich habe gerade ein technisches Problem mit der KI-Verbindung. "
    "Einen Moment bitte."
)
_GOODBYE = (
    "Entschuldigung, die Sprachverbindung zur KI funktioniert leider nicht. "
    "Bitte versuchen Sie es später erneut. Auf Wiederhören."
)


def _is_llm_error(frame: ErrorFrame) -> bool:
    if not frame.processor:
        return False
    name = type(frame.processor).__name__
    return "LLM" in name or "llm" in name.lower()


class LlmFailureProcessor(FrameProcessor):
    """Speak on LLM completion errors (upstream ErrorFrame); hang up after two in a row."""

    def __init__(
        self,
        *,
        hooks: M2CallHooks,
        session: M2CallSession | None = None,
        settings: Settings | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._hooks = hooks
        self._session = session
        self._apology = phrase(settings, "llm_failure_apology") if settings is not None else _APOLOGY
        self._goodbye = phrase(settings, "llm_failure_goodbye") if settings is not None else _GOODBYE
        self._consecutive_errors = 0
        self._ending = False

    async def _handle_llm_error(self, frame: ErrorFrame) -> None:
        if self._ending:
            return
        if self._session is not None and (
            self._session.phase.suppresses_llm or self._session.phase.apology_played
        ):
            LOG.info("Skipping LLM failure apology; call ending or delay apology already played")
            return
        self._consecutive_errors += 1
        LOG.warning(
            "LLM completion error (%s in a row): %s",
            self._consecutive_errors,
            frame.error,
        )
        if self._consecutive_errors >= 2:
            self._ending = True
            await self.push_frame(TTSSpeakFrame(text=self._goodbye), FrameDirection.DOWNSTREAM)
            await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            await self._hooks.hangup()
        else:
            await self.push_frame(TTSSpeakFrame(text=self._apology), FrameDirection.DOWNSTREAM)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, ErrorFrame) and _is_llm_error(frame):
            await self._handle_llm_error(frame)

        if isinstance(frame, LLMTextFrame) and (frame.text or "").strip():
            self._consecutive_errors = 0

        if isinstance(frame, LLMFullResponseEndFrame):
            # Successful empty/tool-only turns still end cleanly; do not reset on errors
            # that never produced text (counter only resets on LLMTextFrame above).
            pass

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
