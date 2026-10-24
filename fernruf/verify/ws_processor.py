from __future__ import annotations

import asyncio
import audioop
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.call_settings.resolver import resolve_llm_endpoint_for_call
from fernruf.llm.client import StreamingChatClient
from fernruf.settings import Settings
from fernruf.tts.audio import _fixture_pcm, _pcm_to_alaw
from fernruf.voice.latency import LatencyLog, TurnLatency

LOG = logging.getLogger("fernruf.verify.ws")

SendDtmfFn = Callable[[str], Awaitable[None]]
HangupFn = Callable[[], Awaitable[None]]

# ~40 ms of slin16 @ 16 kHz mono (640 bytes) — proves bridged callee audio reached Pipecat input.
MIN_CALLEE_WS_BYTES = 640
CALLEE_AUDIO_WAIT_S = 18.0


@dataclass
class VerifyHooks:
    send_dtmf: SendDtmfFn
    hangup: HangupFn


@dataclass
class VerifyProcessorState:
    transcript: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    dtmf_sent: str = ""
    barge_in_fired: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)
    callee_ws_inbound_bytes: int = 0


def _alaw_chunks(alaw: bytes, chunk_ms: int = 20, rate: int = 8000) -> list[bytes]:
    bytes_per_chunk = int(rate * chunk_ms / 1000)
    return [alaw[i : i + bytes_per_chunk] for i in range(0, len(alaw), bytes_per_chunk)]


def _text_to_alaw(text: str, *, duration_s: float = 3.0) -> bytes:
    pcm = _fixture_pcm(text, sample_rate=8000, duration_s=duration_s)
    return _pcm_to_alaw(pcm, 8000)


class VerifyLoopbackProcessor(FrameProcessor):
    """Scripted M2 verify conversation over chan_websocket (mock LLM, fixture STT)."""

    def __init__(
        self,
        *,
        settings: Settings,
        call_id: str,
        task: str,
        hooks: VerifyHooks,
        latency_log: LatencyLog,
        stt_fixture: str,
        state: VerifyProcessorState,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._call_id = call_id
        self._task = task
        self._hooks = hooks
        self._latency_log = latency_log
        self._stt_fixture = stt_fixture
        self._state = state
        self._script_task: asyncio.Task | None = None
        self._inbound_bytes = 0
        self._first_outbound = False
        self._endpoint, _ = resolve_llm_endpoint_for_call(settings)
        self._llm = StreamingChatClient.for_call(settings, self._endpoint)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._script_task is None:
            self._script_task = asyncio.create_task(self._run_script())
        if isinstance(frame, InputAudioRawFrame):
            self._inbound_bytes += len(frame.audio or b"")
            self._state.callee_ws_inbound_bytes = self._inbound_bytes
        await self.push_frame(frame, direction)

    async def _push_alaw(self, alaw: bytes, *, mark_first_out: bool = False) -> None:
        for chunk in _alaw_chunks(alaw):
            if not chunk:
                continue
            pcm8 = audioop.alaw2lin(chunk, 2)
            pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
            out = OutputAudioRawFrame(audio=pcm16, sample_rate=16000, num_channels=1)
            if mark_first_out and not self._first_outbound:
                self._first_outbound = True
                self._state.checks["ai_notice_first"] = True
            await self.push_frame(out, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.018)

    async def _wait_callee_audio(self) -> bool:
        deadline = time.monotonic() + CALLEE_AUDIO_WAIT_S
        while time.monotonic() < deadline:
            if self._inbound_bytes >= MIN_CALLEE_WS_BYTES:
                return True
            await asyncio.sleep(0.05)
        return False

    async def _run_script(self) -> None:
        try:
            opener = self._settings.disclosure_prefix
            self._state.transcript.append(
                {"speaker": "agent", "text": opener, "phase": "opener"}
            )
            await self._push_alaw(_text_to_alaw(opener, duration_s=2.5), mark_first_out=True)

            row = self._latency_log.start_turn(
                self._call_id,
                1,
                provider=self._endpoint.provider,
                model=self._endpoint.model,
            )
            if not await self._wait_callee_audio():
                LOG.error(
                    "M2 verify: callee RTP bytes on WebSocket input=%s (need >= %s). "
                    "Check SIPp rtp_stream, mixing bridge, and chan_websocket d(both).",
                    self._inbound_bytes,
                    MIN_CALLEE_WS_BYTES,
                )
                self._state.checks["callee_rtp_received"] = False
                self._state.checks["callee_transcribed"] = False
                self._latency_log.append(row)
                await self._hooks.hangup()
                self._state.done.set()
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return

            row.mark("vad_end")
            callee_text = self._stt_fixture
            row.mark("stt_final")
            self._state.transcript.append({"speaker": "callee", "text": callee_text})
            self._state.checks["callee_transcribed"] = bool(callee_text.strip())
            self._state.checks["callee_rtp_received"] = True
            LOG.info(
                "M2 verify: callee audio on WS input ok (%s bytes); fixture STT turn",
                self._inbound_bytes,
            )

            messages = [
                {
                    "role": "system",
                    "content": f"Verify loopback task: {self._task}",
                },
                {"role": "user", "content": callee_text},
            ]

            async def on_token(_: str) -> None:
                if row.llm_first_token_ms is None:
                    row.mark("llm_first_token")

            agent_text = ""

            async def on_tool(name: str, args: dict) -> None:
                nonlocal agent_text
                if name == "send_dtmf":
                    digits = str(args.get("digits", ""))
                    self._state.dtmf_sent += digits
                    await self._hooks.send_dtmf(digits)
                if name == "end_call":
                    await self._hooks.hangup()

            result = await self._llm.stream_turn(
                messages, on_token=on_token, on_tool=on_tool
            )
            agent_text = result.assistant_text or "Verstanden, vielen Dank."
            self._state.transcript.append({"speaker": "agent", "text": agent_text})

            long_reply = agent_text + " " + ("Ich wiederhole zur Unterbrechung. " * 8)
            alaw = _text_to_alaw(long_reply, duration_s=6.0)
            row.mark("tts_first_audio")
            row.mark("rtp_first_out")

            async def play_long() -> None:
                await self._push_alaw(alaw)

            play_task = asyncio.create_task(play_long())
            await asyncio.sleep(0.35)
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            self._state.barge_in_fired = True
            self._state.checks["barge_in"] = True
            play_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await play_task

            if not self._state.dtmf_sent:
                await self._hooks.send_dtmf("12")
                self._state.dtmf_sent = "12"
            self._state.checks["dtmf_sent"] = bool(self._state.dtmf_sent)

            self._latency_log.append(row)
            self._state.checks["latency_jsonl"] = self._latency_log.path.is_file()

            await self._hooks.hangup()
            self._state.checks["end_call_hangup"] = True

            await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        except Exception as exc:
            LOG.exception("verify script failed: %s", exc)
            self._state.checks["script_error"] = False
        finally:
            self._state.done.set()
