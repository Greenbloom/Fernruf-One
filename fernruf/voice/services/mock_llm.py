from __future__ import annotations

import logging
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.hooks import M2CallHooks

LOG = logging.getLogger("fernruf.voice.llm.mock")

DtmfTap = Callable[[str], Awaitable[None]] | None


class MockLLMService(FrameProcessor):
    """Deterministic LLM for verify / CI (tools: send_dtmf, end_call)."""

    def __init__(
        self,
        *,
        hooks: M2CallHooks,
        task: str,
        on_dtmf: DtmfTap = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._hooks = hooks
        self._task = task
        self._on_dtmf = on_dtmf
        self._turn = 0
        self._dtmf_sent = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        self._turn += 1
        if self._turn == 1:
            text = (
                "Danke für Ihre Zeit. Ich habe alles notiert. "
                "Gibt es noch etwas Wichtiges?"
            )
        else:
            text = "Vielen Dank, auf Wiederhören."

        LOG.info("LLM (mock) turn=%s: %s", self._turn, text)
        await self.push_frame(LLMFullResponseStartFrame(), direction)
        await self.push_frame(LLMTextFrame(text=text), direction)
        await self.push_frame(LLMFullResponseEndFrame(), direction)

        if self._turn == 1 and not self._dtmf_sent:
            call_id = "mock-send-dtmf"
            digits = "12"
            await self.push_frame(
                FunctionCallInProgressFrame(
                    function_name="send_dtmf",
                    tool_call_id=call_id,
                    arguments={"digits": digits},
                ),
                direction,
            )
            await self._hooks.send_dtmf(digits)
            if self._on_dtmf:
                await self._on_dtmf(digits)
            self._dtmf_sent = True
            await self.push_frame(
                FunctionCallResultFrame(
                    function_name="send_dtmf",
                    tool_call_id=call_id,
                    arguments={"digits": digits},
                    result={"ok": True},
                ),
                direction,
            )

        if self._turn >= 2:
            call_id = "mock-end-call"
            await self.push_frame(
                FunctionCallInProgressFrame(
                    function_name="end_call",
                    tool_call_id=call_id,
                    arguments={"reason": "mock_complete"},
                ),
                direction,
            )
            await self._hooks.hangup()
            await self.push_frame(
                FunctionCallResultFrame(
                    function_name="end_call",
                    tool_call_id=call_id,
                    arguments={"reason": "mock_complete"},
                    result={"ok": True},
                ),
                direction,
            )
