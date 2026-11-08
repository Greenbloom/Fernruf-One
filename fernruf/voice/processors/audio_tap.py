"""Read-only copy of call audio. A slow listener never waits inside the pipeline."""

from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, InputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.monitor.hub import CallMonitor

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
    monitor.offer_audio(
        call_id,
        direction=direction,
        pcm=pcm,
        sample_rate=int(frame.sample_rate),
        channels=int(frame.num_channels),
    )


class AudioTapProcessor(FrameProcessor):
    def __init__(self, monitor: CallMonitor, call_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._monitor = monitor
        self._call_id = call_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        try:
            note_frame(self._monitor, self._call_id, frame)
        except Exception:
            LOG.warning("audio tap failed")
        await self.push_frame(frame, direction)
