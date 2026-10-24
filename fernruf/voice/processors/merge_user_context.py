from __future__ import annotations

import logging
from typing import Any

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import message_text

LOG = logging.getLogger("fernruf.voice.merge_user")


def merge_consecutive_assistant_messages(messages: list[Any]) -> list[Any]:
    """Drop trailing duplicate assistant messages with identical content."""
    if not messages:
        return messages
    merged: list[Any] = list(messages)
    while len(merged) >= 2:
        a = message_text(merged[-1])
        b = message_text(merged[-2])
        role_a = (
            merged[-1].get("role")
            if isinstance(merged[-1], dict)
            else getattr(merged[-1], "role", None)
        )
        role_b = (
            merged[-2].get("role")
            if isinstance(merged[-2], dict)
            else getattr(merged[-2], "role", None)
        )
        if role_a == "assistant" and role_b == "assistant" and a and a == b:
            merged.pop()
            continue
        break
    return merged


def merge_consecutive_user_messages(messages: list[Any]) -> list[Any]:
    """Merge trailing user messages with no assistant message between them."""
    if not messages:
        return messages
    merged: list[Any] = []
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        cur = message_text(msg)
        if role == "user" and merged:
            prev_role = (
                merged[-1].get("role")
                if isinstance(merged[-1], dict)
                else getattr(merged[-1], "role", None)
            )
            prev = message_text(merged[-1])
            if prev_role == "user":
                if cur and cur == prev:
                    continue
                combined = f"{prev} {cur}".strip() if prev else cur
                if isinstance(merged[-1], dict):
                    merged[-1] = {**merged[-1], "content": combined}
                else:
                    merged[-1] = {"role": "user", "content": combined}
                continue
        merged.append(msg)
    return merged


class MergeConsecutiveUserContextProcessor(FrameProcessor):
    """Collapse consecutive user context entries before the LLM reads them."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMContextFrame) and isinstance(frame.context, LLMContext):
            messages = list(getattr(frame.context, "messages", []) or [])
            merged = merge_consecutive_user_messages(messages)
            merged = merge_consecutive_assistant_messages(merged)
            if merged != messages:
                LOG.info(
                    "Merged consecutive user messages (%s -> %s user tail)",
                    len(messages),
                    len(merged),
                )
                frame.context.set_messages(merged)

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
