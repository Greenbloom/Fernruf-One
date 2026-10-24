from __future__ import annotations

import logging
import time

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.frames.metrics_frames import (
    FishTtsFirstAudioFrame,
    LlmFirstTokenFrame,
    LlmInferenceStartedFrame,
)
from fernruf.voice.latency import LatencyLog, TurnLatency

LOG = logging.getLogger("fernruf.voice.metrics")


class ConversationMetricsProcessor(FrameProcessor):
    """INFO logging + per-turn latency JSONL."""

    def __init__(
        self,
        *,
        call_id: str,
        provider: str,
        model: str,
        latency_log: LatencyLog | None,
        session: M2CallSession | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._call_id = call_id
        self._provider = provider
        self._model = model
        self._latency_log = latency_log
        self._session = session
        self._turn = 0
        self._row: TurnLatency | None = None
        self._llm_buf = ""
        self._llm_turn_complete = False
        self._expect_assistant_tts = False
        self.inbound_bytes = 0
        self.outbound_bytes = 0

    def _ensure_row(self) -> TurnLatency | None:
        if self._row is not None or not self._latency_log:
            return self._row
        self._turn += 1
        self._row = self._latency_log.start_turn(
            self._call_id,
            self._turn,
            provider=self._provider,
            model=self._model,
        )
        return self._row

    def _prior_turn_ready_to_commit(self) -> bool:
        if self._row is None:
            return False
        has_user = "user_turn_stop" in self._row._marks or "stt_final" in self._row._marks
        has_bot = (
            "llm_request_start" in self._row._marks
            or "llm_first_token" in self._row._marks
            or self._llm_turn_complete
        )
        return has_user and has_bot

    def _mark_user_turn_stop(self) -> None:
        if self._prior_turn_ready_to_commit():
            self._commit_row()
        now = time.monotonic()
        if self._session is not None:
            self._session.last_user_turn_stop_mono = now
        row = self._ensure_row()
        if row and "user_turn_stop" not in row._marks:
            row._marks["user_turn_stop"] = now

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InputAudioRawFrame):
            self.inbound_bytes += len(frame.audio or b"")

        if isinstance(frame, LLMMessagesAppendFrame):
            for msg in frame.messages or []:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = (msg.get("content") or "").strip()
                    if content:
                        self._mark_user_turn_stop()
                        row = self._row
                        if row and "stt_final" not in row._marks:
                            row.mark("stt_final")

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._mark_user_turn_stop()

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._mark_user_turn_stop()
            row = self._row
            if row:
                row.mark("stt_final")
            LOG.info("STT final: %s", frame.text.strip())

        if isinstance(frame, LlmInferenceStartedFrame):
            row = self._ensure_row()
            if row:
                row.mark("llm_request_start")

        if isinstance(frame, LLMFullResponseStartFrame):
            self._llm_buf = ""
            self._llm_turn_complete = False
            self._expect_assistant_tts = False
            LOG.info("LLM turn start model=%s", self._model)

        if isinstance(frame, (LLMTextFrame, LlmFirstTokenFrame)):
            if isinstance(frame, LLMTextFrame):
                self._llm_buf += frame.text
            self._expect_assistant_tts = True
            row = self._ensure_row()
            if row and "llm_first_token" not in row._marks:
                row.mark("llm_first_token")

        if isinstance(frame, FishTtsFirstAudioFrame):
            row = self._ensure_row()
            if row and self._expect_assistant_tts and "fish_ttfa" not in row._marks:
                row.mark("fish_ttfa")
                row.mark("tts_first_audio")

        if isinstance(frame, TTSAudioRawFrame):
            self.outbound_bytes += len(frame.audio or b"")
            row = self._ensure_row()
            if row and self._expect_assistant_tts and "rtp_first_out" not in row._marks:
                row.mark("rtp_first_out")

        if isinstance(frame, LLMFullResponseEndFrame):
            self._llm_turn_complete = True
            if not self._expect_assistant_tts:
                self._commit_row()

        if isinstance(frame, TTSStoppedFrame):
            if self._expect_assistant_tts and self._llm_turn_complete:
                self._commit_row()

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    def _commit_row(self) -> None:
        if self._row and self._latency_log:
            row = self._row
            row.finalize_deltas()
            has_signal = any(
                k in row._marks
                for k in (
                    "stt_final",
                    "llm_first_token",
                    "fish_ttfa",
                    "rtp_first_out",
                    "llm_request_start",
                )
            )
            if not has_signal:
                self._row = None
                return
            if row.llm_first_token_ms is not None:
                LOG.info(
                    "turn latency: user_stop→llm_first_token=%.0fms",
                    row.llm_first_token_ms,
                )
            http_ttft = row.delta_ms("llm_request_start", "llm_first_token")
            if http_ttft is not None and http_ttft >= 0:
                LOG.info("turn latency: llm_http→first_token=%.0fms", http_ttft)
            if row.llm_to_tts_first_audio_ms is not None:
                LOG.info(
                    "turn latency: llm_first_token→fish_ttfa=%.0fms",
                    row.llm_to_tts_first_audio_ms,
                )
            fish = row.fish_ttfa_ms
            if fish is not None and fish < 0:
                row.fish_ttfa_ms = None
                row.rtp_first_out_ms = None
            if row.stt_final_ms is not None and row.stt_final_ms < 0:
                row.stt_final_ms = None
            LOG.info(
                "turn latency chain: stt_final=%s ms llm_first=%s ms fish_ttfa=%s ms rtp_out=%s ms",
                f"{row.stt_final_ms:.0f}" if row.stt_final_ms is not None else "—",
                f"{row.llm_first_token_ms:.0f}" if row.llm_first_token_ms is not None else "—",
                f"{row.fish_ttfa_ms:.0f}" if row.fish_ttfa_ms is not None else "—",
                f"{row.rtp_first_out_ms:.0f}" if row.rtp_first_out_ms is not None else "—",
            )
            self._latency_log.append(row)
            self._row = None
            self._llm_turn_complete = False
            self._expect_assistant_tts = False

    def flush_turn(self) -> None:
        self._commit_row()
