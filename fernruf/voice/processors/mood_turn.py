"""Start a Jev mood score when a user turn is final. Does not wait on it."""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.mood import MoodTurn, last_user_and_prior, prior_bot_line
from fernruf.voice.call_session import M2CallSession, message_text


class MoodTurnProcessor(FrameProcessor):
    """Start Jev as soon as the final user transcript is available."""

    def __init__(self, turn: MoodTurn, session: M2CallSession, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._turn = turn
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if text:
                self._turn.start(text, prior_bot=prior_bot_line(self._session))
        elif isinstance(frame, LLMContextFrame) and getattr(frame, "context", None) is not None:
            messages = list(getattr(frame.context, "messages", []) or [])
            utterance, prior = last_user_and_prior(messages)
            if utterance:
                self._turn.start(utterance, prior_bot=prior)
        elif (
            isinstance(frame, LLMMessagesAppendFrame)
            and direction == FrameDirection.UPSTREAM
            and bool(getattr(frame, "run_llm", False))
        ):
            for msg in getattr(frame, "messages", None) or []:
                role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
                if role != "user":
                    continue
                text = message_text(msg)
                if text:
                    self._turn.start(text, prior_bot=prior_bot_line(self._session))
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
