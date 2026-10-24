from __future__ import annotations

from typing import Any

from pipecat.frames.frames import Frame, LLMContextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession, message_text
from fernruf.voice.processors.merge_user_context import merge_consecutive_user_messages


def _spoken_pairs(messages: list[Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        text = message_text(msg)
        if not text:
            continue
        if role == "assistant":
            pairs.append(("agent", text))
        elif role == "user":
            pairs.append(("callee", text))
    return pairs


class ContextSessionSyncProcessor(FrameProcessor):
    """Keep :class:`M2CallSession` user-turn state in sync with committed LLM context."""

    def __init__(self, *, session: M2CallSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMContextFrame) and frame.context is not None:
            messages: list[Any] = list(getattr(frame.context, "messages", []) or [])
            merged = merge_consecutive_user_messages(messages)
            if merged != messages:
                frame.context.set_messages(merged)
                messages = merged
            self._session.sync_from_llm_messages(messages)
            self._session.observe_committed_lines(_spoken_pairs(messages))

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._session.note_user_transcript(frame.text.strip())

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
