"""Realtime playback of bursty call audio.

Outbound TTS arrives faster than a listener can play it. Writing each chunk
as it arrives fills the device and drops the overflow, so the agent side
sounds chopped while the callee side, already paced, does not. This clock
holds a short preroll, then releases one frame per frame period.
"""

from __future__ import annotations

import array
import time
from collections.abc import Callable

MONITOR_RATE = 16000
MONITOR_CHANNELS = 1
FRAME_MS = 20
PREROLL_MS = 60


def to_s16_mono(
    pcm: bytes,
    sample_rate: int,
    channels: int,
    *,
    rate: int = MONITOR_RATE,
) -> bytes:
    """16 kHz mono s16le. Both monitor legs use this one format."""
    if not pcm or sample_rate <= 0 or channels <= 0:
        return b""
    width = 2 * channels
    usable = pcm[: len(pcm) - (len(pcm) % width)]
    if not usable:
        return b""
    samples = array.array("h")
    samples.frombytes(usable)
    if channels > 1:
        samples = array.array("h", samples[::channels])
    if sample_rate == rate or len(samples) == 0:
        return samples.tobytes()
    dest_len = max(1, int(round(len(samples) * rate / sample_rate)))
    if dest_len == len(samples):
        return samples.tobytes()
    out = array.array("h", [0]) * dest_len
    if len(samples) == 1 or dest_len == 1:
        out[0] = samples[0]
        return out.tobytes()
    scale = (len(samples) - 1) / (dest_len - 1)
    for index in range(dest_len):
        pos = index * scale
        left = int(pos)
        right = min(left + 1, len(samples) - 1)
        frac = pos - left
        mixed = samples[left] * (1.0 - frac) + samples[right] * frac
        out[index] = int(max(-32768, min(32767, round(mixed))))
    return out.tobytes()


class PlaybackPacer:
    """Release s16le mono at ``rate``. A burst does not play faster than realtime."""

    def __init__(
        self,
        *,
        rate: int = MONITOR_RATE,
        channels: int = MONITOR_CHANNELS,
        frame_ms: int = FRAME_MS,
        preroll_ms: int = PREROLL_MS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.rate = rate
        self.channels = channels
        self.frame_ms = frame_ms
        self._frame_s = frame_ms / 1000.0
        self._frame_bytes = max(2, rate * channels * 2 * frame_ms // 1000)
        self._preroll_frames = max(1, preroll_ms // frame_ms)
        self._preroll_bytes = self._frame_bytes * self._preroll_frames
        self._clock = clock or time.monotonic
        self._buf = bytearray()
        self._started = False
        self._next = 0.0

    def push(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        converted = to_s16_mono(pcm, sample_rate, channels, rate=self.rate)
        if converted:
            self._buf.extend(converted)

    def take(self, now: float | None = None) -> list[bytes]:
        """Frames whose presentation time has arrived. Empty while preroll fills."""
        now = self._clock() if now is None else now
        if not self._started:
            if len(self._buf) < self._preroll_bytes:
                return []
            self._started = True
            frames = self._pop(self._preroll_frames)
            self._next = now + self._frame_s
            return frames
        frames: list[bytes] = []
        while now + 1e-6 >= self._next and len(self._buf) >= self._frame_bytes:
            frames.extend(self._pop(1))
            self._next += self._frame_s
            if len(frames) >= self._preroll_frames:
                break
        return frames

    def pending(self) -> int:
        return len(self._buf)

    def _pop(self, count: int) -> list[bytes]:
        frames: list[bytes] = []
        for _ in range(count):
            if len(self._buf) < self._frame_bytes:
                break
            frames.append(bytes(self._buf[: self._frame_bytes]))
            del self._buf[: self._frame_bytes]
        return frames
