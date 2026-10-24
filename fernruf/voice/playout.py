"""Playout clock for audio that has been handed to the output transport.

Asterisk's media websocket does not play in real time. The transport accepts
chunks immediately and Asterisk buffers them. ``BotStoppedSpeaking`` means the
pipeline finished writing, not that the phone finished playing.

Each accepted chunk extends the running clock::

    playout_end = max(playout_end, now) + chunk_duration

Duration is ``num_bytes / (sample_rate * channels * width)``. Audio already
played (``playout_end`` in the past) does not stretch the next chunk.

The final turn is also summed on its own clock, started at the first chunk
after the last interruption and never restarted when a later sentence in that
turn begins TTS::

    final_turn_end = first_write + sum(chunk durations)

A fast TTS burst and a cache hit are in that sum. A gap only moves the end
later, via the running clock.
"""

from __future__ import annotations

import time


class PlayoutClock:
    """Monotonic estimate of when queued output audio finishes on the phone."""

    def __init__(self) -> None:
        self._end = 0.0
        # Final turn since the last reset (barge-in / cancel / stop).
        # The running ``end`` clock starts each chunk at max(end, now), so once
        # audio streams in near real time ``remaining`` is only the tail. The
        # phone still has every sample written this turn, from the first write.
        self._utt_first: float | None = None
        self._utt_total = 0.0

    def begin_utterance(self) -> None:
        """Do not drop audio already counted.

        A later TTS context in the same turn (the goodbye after "Vielen Dank")
        is still the same playout. Resetting here made the logged duration 0
        when the goodbye itself was a cache hit that had not been written yet.
        """
        return

    def add_chunk(
        self,
        num_bytes: int,
        sample_rate: int,
        *,
        channels: int = 1,
        width: int = 2,
    ) -> float:
        """Extend the clock by one chunk. Returns the chunk duration in seconds."""
        if num_bytes <= 0 or sample_rate <= 0 or channels <= 0 or width <= 0:
            return 0.0
        duration = num_bytes / (sample_rate * channels * width)
        now = time.monotonic()
        self._end = max(self._end, now) + duration
        if self._utt_first is None:
            self._utt_first = now
        self._utt_total += duration
        return duration

    def reset(self) -> None:
        """Drop queued playout. Used on barge-in, cancel, and stop."""
        self._end = 0.0
        self._utt_first = None
        self._utt_total = 0.0

    @property
    def utterance_first(self) -> float | None:
        """Monotonic time of the first chunk written since the last reset."""
        return self._utt_first

    @property
    def utterance_total(self) -> float:
        """Sum of chunk durations written since the last reset."""
        return self._utt_total

    def goodbye_audio_end(self) -> float:
        """When the audio written since the last reset finishes, before margin.

        ``first_write + total_duration`` counts every sample of the turn,
        including audio that was written faster than real time. The running
        clock end is included so a gap between chunks cannot hang up early.
        """
        ends: list[float] = []
        if self._utt_first is not None and self._utt_total > 0:
            ends.append(self._utt_first + self._utt_total)
        if self._end > 0:
            ends.append(self._end)
        if not ends:
            return time.monotonic()
        return max(ends)

    def remaining(self, now: float | None = None) -> float:
        """Seconds until ``playout_end``. Zero once that time has passed."""
        if now is None:
            now = time.monotonic()
        return max(0.0, self._end - now)

    @property
    def end(self) -> float:
        """Monotonic time when the last accepted chunk finishes playing."""
        return self._end
