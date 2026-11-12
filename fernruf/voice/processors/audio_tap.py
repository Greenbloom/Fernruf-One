"""Read-only copy of call audio. A slow listener never waits inside the pipeline."""

from __future__ import annotations

import asyncio
import logging

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.monitor.hub import CallMonitor
from fernruf.monitor.pace import MONITOR_RATE, to_s16_mono

LOG = logging.getLogger("fernruf.voice.audio_tap")


def note_frame(monitor: CallMonitor, call_id: str, frame: Frame) -> None:
    if not monitor.wants_audio():
        return
    if isinstance(frame, InputAudioRawFrame):
        direction = "inbound"
    elif isinstance(frame, TTSAudioRawFrame):
        direction = "outbound"
    else:
        return
    pcm = frame.audio or b""
    if not pcm:
        return
    sample_rate = int(frame.sample_rate)
    channels = int(frame.num_channels)
    if direction == "outbound":
        # TTS is bursty. Re-clock it here so the socket is not one fast dump.
        monitor.offer_paced_outbound(
            call_id,
            pcm,
            sample_rate=sample_rate,
            channels=channels,
        )
        return
    paced = to_s16_mono(pcm, sample_rate, channels)
    if not paced:
        return
    monitor.offer_audio(
        call_id,
        direction=direction,
        pcm=paced,
        sample_rate=MONITOR_RATE,
        channels=1,
    )


class AudioTapProcessor(FrameProcessor):
    def __init__(self, monitor: CallMonitor, call_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._monitor = monitor
        self._call_id = call_id
        self._pump: asyncio.Task | None = None

    def _ensure_pump(self) -> None:
        if self._pump is not None and not self._pump.done():
            return
        try:
            self._pump = self.create_task(self._run_pump(), "monitor_outbound_pace")
        except Exception:
            self._pump = asyncio.get_running_loop().create_task(self._run_pump())

    async def _run_pump(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.02)
                self._monitor.release_outbound(self._call_id)
        except asyncio.CancelledError:
            return

    def _stop_pump(self) -> None:
        task = self._pump
        self._pump = None
        if task is not None and not task.done():
            task.cancel()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._ensure_pump()
        if isinstance(frame, (EndFrame, CancelFrame)):
            self._stop_pump()
        try:
            note_frame(self._monitor, self._call_id, frame)
            if isinstance(frame, TTSAudioRawFrame):
                self._ensure_pump()
        except Exception:
            LOG.warning("audio tap failed")
        await self.push_frame(frame, direction)
