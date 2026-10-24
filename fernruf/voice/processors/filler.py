from __future__ import annotations

import asyncio
import logging
import time

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.frames.metrics_frames import LlmFirstTokenFrame
from fernruf.voice.processors.filler_state import LlmFillerState

LOG = logging.getLogger("fernruf.voice.filler")


class LlmFillerProcessor(FrameProcessor):
    """Play a short filler if the LLM is slow after the user turn completes."""

    def __init__(
        self,
        *,
        phrase: str,
        delay_s: float = 1.2,
        state: LlmFillerState | None = None,
        session: M2CallSession | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._phrase = phrase
        self._delay_s = delay_s
        self._state = state if state is not None else LlmFillerState()
        self._session = session

    def _cancel_wait(self) -> None:
        task = self._state.wait_task
        if task and not task.done():
            task.cancel()
        self._state.wait_task = None

    def _schedule_wait(self) -> None:
        if self._session and self._session.phase.suppresses_llm:
            return
        if self._session and self._session.last_bot_stopped_speaking_mono is not None:
            if time.monotonic() - self._session.last_bot_stopped_speaking_mono < 3.0:
                return
        if self._session and self._session.filler_played_this_turn:
            return
        if self._session and self._session.filler_played_last_turn_without_answer:
            return
        self._cancel_wait()
        self._state.llm_text_seen = False
        coro = self._wait_and_filler()
        tm = getattr(self, "_task_manager", None)
        if tm:
            self._state.wait_task = self.create_task(coro, name="llm_filler_wait")
        else:
            self._state.wait_task = asyncio.get_running_loop().create_task(coro)

    async def _wait_and_filler(self) -> None:
        try:
            await asyncio.sleep(self._delay_s)
        except asyncio.CancelledError:
            return
        if self._session and self._session.phase.suppresses_llm:
            return
        if self._session and self._session.filler_played_this_turn:
            return
        if not self._state.llm_text_seen:
            LOG.info("LLM slow (>%ss); playing filler", self._delay_s)
            if self._session:
                self._session.filler_played_this_turn = True
            await self.push_frame(
                TTSSpeakFrame(text=self._phrase, append_to_context=False),
                FrameDirection.DOWNSTREAM,
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMFullResponseStartFrame):
            if self._session:
                self._session.filler_played_this_turn = False
                self._session.speakable_llm_this_turn = False
            self._schedule_wait()

        if isinstance(frame, LLMFullResponseEndFrame) and self._session:
            if self._session.filler_played_this_turn and not self._session.speakable_llm_this_turn:
                self._session.filler_played_last_turn_without_answer = True
            else:
                self._session.filler_played_last_turn_without_answer = False
            self._session.speakable_llm_last_turn = self._session.speakable_llm_this_turn

        if isinstance(frame, UserStartedSpeakingFrame):
            self._cancel_wait()

        if isinstance(frame, (LLMTextFrame, LlmFirstTokenFrame)):
            self._state.llm_text_seen = True
            self._cancel_wait()

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
