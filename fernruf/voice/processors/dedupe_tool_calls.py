from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

LOG = logging.getLogger("fernruf.voice.dedupe_tool_calls")


class DedupeToolCallFramesProcessor(FrameProcessor):
    """Drop duplicate function-call aggregator frames (same tool_call_id)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._seen_started: set[str] = set()
        self._seen_in_progress: set[str] = set()
        self._seen_result: set[str] = set()

    @staticmethod
    def _tool_ids(frame: Frame) -> list[str]:
        if isinstance(frame, FunctionCallsStartedFrame):
            return [tc.tool_call_id for tc in frame.function_calls if tc.tool_call_id]
        if isinstance(frame, (FunctionCallInProgressFrame, FunctionCallResultFrame)):
            return [frame.tool_call_id] if frame.tool_call_id else []
        return []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, FunctionCallsStartedFrame):
            ids = self._tool_ids(frame)
            if ids and all(i in self._seen_started for i in ids):
                LOG.debug("Dropping duplicate FunctionCallsStartedFrame %s", ids)
                return
            for i in ids:
                self._seen_started.add(i)
        elif isinstance(frame, FunctionCallInProgressFrame):
            tid = frame.tool_call_id
            if tid and tid in self._seen_in_progress:
                LOG.debug("Dropping duplicate FunctionCallInProgressFrame %s", tid)
                return
            if tid:
                self._seen_in_progress.add(tid)
        elif isinstance(frame, FunctionCallResultFrame):
            tid = frame.tool_call_id
            if tid and tid in self._seen_result:
                LOG.debug("Dropping duplicate FunctionCallResultFrame %s", tid)
                return
            if tid:
                self._seen_result.add(tid)

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
