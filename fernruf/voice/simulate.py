from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fernruf.call_record import remember_call
from fernruf.call_settings.resolver import resolve_llm_endpoint_for_call
from fernruf.language import prepare_call_settings
from fernruf.llm.client import StreamingChatClient
from fernruf.settings import Settings
from fernruf.voice.events import GLOBAL_EVENT_BUS
from fernruf.voice.latency import LatencyLog
from fernruf.voice.prompt import build_system_prompt
from fernruf.tts.rest_stream import stream_tts_sentences

LOG = logging.getLogger("fernruf.voice.simulate")


@dataclass
class TranscriptLine:
    t_ms: int
    speaker: str
    text: str


@dataclass
class SimulateResult:
    call_id: str
    transcript: list[TranscriptLine] = field(default_factory=list)
    dtmf_sent: list[str] = field(default_factory=list)
    ended_reason: str = "completed"
    latency_path: Path | None = None
    median: dict[str, float | None] = field(default_factory=dict)
    llm_model: str = ""
    llm_model_source: str = ""
    llm_provider: str = ""
    call_language: str = "de"


class SimulateConversation:
    """Offline M2 loop for CI (mock STT/LLM/TTS timing)."""

    def __init__(
        self,
        settings: Settings,
        task: str,
        *,
        llm_model_override: str | None = None,
        language: str | None = None,
        detection_text: str | None = None,
    ) -> None:
        self.settings = settings
        self.task = task
        self.language = language
        self.detection_text = detection_text
        self.call_id = str(uuid.uuid4())[:8]
        self.bus = GLOBAL_EVENT_BUS
        self.endpoint, self.llm_resolution = resolve_llm_endpoint_for_call(
            settings, call_model_override=llm_model_override
        )
        self.latency = LatencyLog(Path(settings.latency_log_path))
        self.llm = StreamingChatClient.for_call(settings, self.endpoint)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(settings, task)},
        ]
        self.transcript: list[TranscriptLine] = []
        self._start = time.monotonic()
        self._turn = 0
        self._speaking = False
        self._end = False
        self.dtmf_sent: list[str] = []

    def _t_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def _say_agent(self, text: str) -> None:
        self.transcript.append(TranscriptLine(self._t_ms(), "agent", text))
        self.bus.emit(self.call_id, "transcript", speaker="agent", text=text)

    async def run(self, callee_script: list[str] | None = None) -> SimulateResult:
        if not getattr(self.settings, "_language_ready", False):
            self.settings = await prepare_call_settings(
                self.settings,
                self.task,
                language=self.language,
                detection_text=self.detection_text,
            )
            self.messages[0]["content"] = build_system_prompt(self.settings, self.task)
        if hasattr(self.llm, "language"):
            self.llm.language = self.settings.call_language or "de"
        remember_call(
            self.settings,
            call_id=self.call_id,
            call_language=self.settings.call_language,
            task=self.task,
            status="simulated",
        )
        script = callee_script or ["Hallo?", "Ja, Dienstag passt."]
        self.bus.emit(self.call_id, "state", state="answered")
        from fernruf.disclosure import spoken_ai_disclosure

        opener = (self.settings.disclosure_prefix or "").strip() or spoken_ai_disclosure(
            self.settings
        )
        self._say_agent(opener)
        privacy = (self.settings.spoken_privacy_line or "").strip()
        if privacy:
            self._say_agent(privacy)
        self.bus.emit(self.call_id, "state", state="speaking_opener")

        for callee_text in script:
            await asyncio.sleep(0.05)
            self.transcript.append(TranscriptLine(self._t_ms(), "callee", callee_text))
            self.bus.emit(self.call_id, "transcript", speaker="callee", text=callee_text)
            self._turn += 1
            row = self.latency.start_turn(
                self.call_id,
                self._turn,
                provider=self.endpoint.provider,
                model=self.endpoint.model,
            )
            row.simulated = True
            row.mark("vad_end")
            row.mark("stt_final")
            self.messages.append({"role": "user", "content": callee_text})

            async def on_token(_: str) -> None:
                if row.llm_first_token_ms is None:
                    row.mark("llm_first_token")

            async def on_tool(name: str, args: dict) -> None:
                if name == "send_dtmf":
                    digits = str(args.get("digits", ""))
                    self.dtmf_sent.append(digits)
                    self.bus.emit(self.call_id, "dtmf_out", digits=digits)
                if name == "end_call":
                    self._end = True

            self._speaking = True
            result = await self.llm.stream_turn(
                self.messages, on_token=on_token, on_tool=on_tool
            )
            self.messages.append({"role": "assistant", "content": result.assistant_text})
            async for _pcm, _rate, _ttfa in stream_tts_sentences(
                result.assistant_text, settings=self.settings
            ):
                if row.tts_first_audio_ms is None:
                    row.mark("tts_first_audio")
                    row.mark("rtp_first_out")
                if not self._speaking:
                    break
            self._say_agent(result.assistant_text)
            self.latency.append(row)
            lat = row.to_json()
            lat.pop("call_id", None)
            self.bus.emit(self.call_id, "latency", **lat)
            if self._end:
                break

        self.bus.emit(self.call_id, "state", state="finalized")
        median = self.latency.median_report(include_simulated=False)
        return SimulateResult(
            call_id=self.call_id,
            transcript=self.transcript,
            dtmf_sent=self.dtmf_sent,
            latency_path=self.latency.path,
            median=median,
            llm_model=self.llm_resolution.model,
            llm_model_source=self.llm_resolution.source,
            llm_provider=self.endpoint.provider,
            call_language=self.settings.call_language or "de",
        )
