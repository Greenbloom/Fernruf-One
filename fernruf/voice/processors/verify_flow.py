from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.settings import Settings
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.latency import LatencyLog

LOG = logging.getLogger("fernruf.verify.flow")

MIN_CALLEE_WS_BYTES = 640
MIN_AGENT_WS_BYTES = 6400


@dataclass
class VerifyFlowState:
    transcript: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    dtmf_sent: str = ""
    barge_in_fired: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)
    callee_ws_inbound_bytes: int = 0
    agent_ws_outbound_bytes: int = 0
    tts_synth_log: list[str] = field(default_factory=list)


class VerifyFlowProcessor(FrameProcessor):
    """Track verify assertions in-band; orchestration runs outside the processor (pipecat 1.14)."""

    def __init__(
        self,
        *,
        settings: Settings,
        hooks: M2CallHooks,
        latency_log: LatencyLog,
        metrics: Any,
        state: VerifyFlowState,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings
        self._hooks = hooks
        self._latency_log = latency_log
        self._conversation_metrics = metrics
        self._state = state
        self._saw_disclosure = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        notice = (self._settings.disclosure_prefix or "").strip()
        spoken = frame.text if isinstance(frame, TTSSpeakFrame) else ""
        if spoken and (
            (notice and notice in spoken)
            or "KI-Assistent" in spoken
            or "AI assistant" in spoken
        ):
            self._saw_disclosure = True
            self._state.checks["ai_notice_text"] = True
            self._state.checks["ai_notice_first"] = True
            self._state.transcript.append(
                {"speaker": "agent", "text": frame.text, "phase": "opener"}
            )
        if isinstance(frame, TTSStartedFrame) and self._saw_disclosure:
            self._state.checks["ai_notice_first"] = True
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._state.transcript.append({"speaker": "callee", "text": frame.text.strip()})
            self._state.checks["callee_transcribed"] = True
        await self.push_frame(frame, direction)
