"""Asterisk output that knows when queued audio will finish playing.

The stock transport writes into a :class:`FlowController` local buffer.
That controller already paces onto the websocket with bounded lookahead:
Asterisk's chan_websocket buffer is 1000 frames (20 s at 20 ms), the high
water mark stops sending at 80% (16 s), and one websocket message is capped
at 65500 bytes (~2 s of 16 kHz PCM). A fast TTS burst is not dropped there.

What does drop audio is sealing this transport before those bytes are written.
Pipecat queues TTS frames and writes them on a later task. ``TTSStopped`` is
visible upstream while that queue still holds the burst. ``seal_playout``
must wait until the burst has been handed to the flow controller.

Asterisk sends ``QUEUE_DRAINED`` once after ``REPORT_QUEUE_DRAINED``, meaning
it has finished the media it had queued at that moment. The command is sent
only after the local buffer is empty. A drain of a partial queue is not the
phone finishing the turn; the playout clock is the floor.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    StopFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat_asterisk.frames import AsteriskCommandFrame
from pipecat_asterisk.transport.transport import (
    AsteriskWebsocketOutputTransport,
    AsteriskWebsocketTransport,
)

from fernruf.voice.playout import PlayoutClock

LOG = logging.getLogger("fernruf.voice.playout")

_PCM16_WIDTH = 2


class FernrufAsteriskOutputTransport(AsteriskWebsocketOutputTransport):
    """Count outgoing audio and ask Asterisk when its playback queue is empty."""

    def __init__(self, transport, client, params: FastAPIWebsocketParams | None = None, **kwargs):
        super().__init__(transport, client, params, **kwargs)
        self.playout_clock = PlayoutClock()
        # Bumps on every accepted chunk and on flush. A drain request is valid
        # only while this still matches the generation captured at send time.
        self._drain_gen = 0
        self._drain_sent_gen: int | None = None
        self._drain_defer_logged = False
        # Seconds of TTS audio the hangup processor has forwarded but
        # ``write_audio_frame`` has not accepted yet. The pipecat audio task
        # can already have dequeued a frame while this is still positive.
        self._owed_s = 0.0
        # Set once the goodbye utterance has been fully written. Later frames
        # (trailing silence, comfort noise, a late filler) must not extend the
        # clock or invalidate QUEUE_DRAINED.
        self._sealed = False

    def note_audio_queued(self, seconds: float) -> None:
        """Audio passed the hangup processor and is not written yet."""
        if seconds > 0 and not self._sealed:
            self._owed_s += seconds

    def output_audio_pending(self) -> bool:
        """True while final-turn audio can still reach ``write_audio_frame``.

        Sealing before this is false drops the fast-TTS burst: the frames sit
        in the output queue, or have been dequeued but not written.
        """
        if self._owed_s > 0.005:
            return True
        for sender in getattr(self, "_media_senders", {}).values():
            queue = getattr(sender, "_audio_queue", None)
            if queue is not None and not queue.empty():
                return True
            buffered = getattr(sender, "_buffered_audio_bytes", 0) or 0
            if buffered > 0:
                return True
        return False

    def seal_playout(self) -> None:
        """Drop any audio handed over after the goodbye has been written."""
        self._sealed = True

    def playback_idle(self) -> bool:
        """True once the sealed goodbye has been played out.

        The playout clock sums audio from the moment it was accepted, which
        stays ahead of the phone when TTS is faster than realtime. The flow
        controller's remote-buffer estimate is when that audio has actually
        been played. Hangup uses this instead of waiting out the clock.
        """
        if not self._sealed:
            return False
        fc = self._flow_controller
        if fc is None:
            return False
        local = int(getattr(fc, "_local_buffer_size", 0) or 0)
        remote = float(getattr(fc, "_remote_buffer_utilization", 0.0) or 0.0)
        if local > 0 or remote > 0.5:
            return False
        # The flow controller only opens this when utilization hits exactly
        # zero. A drained buffer that stops at a fraction of a byte never
        # does, and the audio task then waits until the 20s cap.
        fence = getattr(fc, "bot_stopped_speaking_fence", None)
        if fence is not None and not fence.is_set():
            fence.set()
        return True

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if isinstance(frame, TTSStoppedFrame):
            await self._wait_for_playout_fence()
        await super(AsteriskWebsocketOutputTransport, self).push_frame(frame, direction)

    async def _wait_for_playout_fence(self) -> None:
        """Wait until Asterisk's buffer has drained, then let the audio task go.

        The stock transport waits with no timeout. A fence that never opens
        leaves the clock and audio tasks running after the pipeline closes.
        """
        fc = self._flow_controller
        fence = getattr(fc, "bot_stopped_speaking_fence", None) if fc is not None else None
        if fence is None or fence.is_set():
            return
        remote = float(getattr(fc, "_remote_buffer_utilization", 0.0) or 0.0)
        psize = int(getattr(fc, "_psize", 640) or 640)
        ptime_ms = int(getattr(fc, "_ptime", 20) or 20)
        seconds = (remote / psize) * (ptime_ms / 1000.0) if psize else 0.0
        timeout = min(8.0, max(0.2, seconds + 0.3))
        try:
            await asyncio.wait_for(fence.wait(), timeout)
        except TimeoutError:
            LOG.warning("Playout fence did not open; releasing the audio task")
            fence.set()

    def _release_playout_wait(self) -> None:
        """Unblock a TTSStopped hold so the output tasks can exit."""
        fc = self._flow_controller
        if fc is None:
            return
        fence = getattr(fc, "bot_stopped_speaking_fence", None)
        if fence is not None:
            fence.set()

    async def _close_flow(self) -> None:
        fc = self._flow_controller
        close = getattr(fc, "close", None) if fc is not None else None
        if close is None:
            return
        try:
            await close(gracefully=False)
        except Exception:
            LOG.warning("flow controller close failed", exc_info=True)

    def _reset_playout(self) -> None:
        self.playout_clock.reset()
        self._owed_s = 0.0
        self._drain_gen += 1
        self._drain_sent_gen = None

    def accept_queue_drained(self) -> bool:
        """True when a ``QUEUE_DRAINED`` matches the request still in effect."""
        return self._drain_sent_gen is not None and self._drain_sent_gen == self._drain_gen

    async def request_queue_drained(self) -> bool:
        """Send ``REPORT_QUEUE_DRAINED`` once the local buffer has been flushed.

        Returns False while audio is still sitting in the flow controller, so
        Asterisk cannot report "drained" before those bytes have been sent.
        A later chunk invalidates the request; the caller retries.
        """
        fc = self._flow_controller
        buffered = int(getattr(fc, "_local_buffer_size", 0) or 0) if fc is not None else 0
        if buffered > 0:
            if not self._drain_defer_logged:
                self._drain_defer_logged = True
                LOG.info(
                    "REPORT_QUEUE_DRAINED waiting; local buffer %s bytes",
                    buffered,
                )
            return False
        self._drain_defer_logged = False
        gen = self._drain_gen
        if self._drain_sent_gen == gen:
            return True
        await self.send_asterisk_command(AsteriskCommandFrame(cmd="REPORT_QUEUE_DRAINED"))
        fc = self._flow_controller
        buffered = int(getattr(fc, "_local_buffer_size", 0) or 0) if fc is not None else 0
        if buffered > 0 or self._drain_gen != gen:
            LOG.info("REPORT_QUEUE_DRAINED not sent; audio arrived while sending")
            return False
        self._drain_sent_gen = gen
        LOG.info("Sent REPORT_QUEUE_DRAINED")
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Flush (FLUSH_MEDIA / drop_buffer) discards audio the phone will
        # never play. Reset before the parent runs so a failed interrupt
        # still drops the clock.
        if isinstance(frame, (InterruptionFrame, CancelFrame, StopFrame, EndFrame)):
            self._reset_playout()
            # End/cancel must not sit behind the playout fence. The audio task
            # holds TTSStopped until that fence, and stop() waits on the audio
            # task, so the clock task would otherwise still be running.
            self._release_playout_wait()
        if isinstance(frame, (CancelFrame, StopFrame, EndFrame)):
            await self._close_flow()
        await super().process_frame(frame, direction)

    async def write_audio_frame(self, frame) -> bool:
        """Count a chunk once the transport has accepted it.

        After ``seal_playout``, the chunk is not sent and does not move the clock.
        Counting at websocket send time would read zero at ``BotStoppedSpeaking``,
        because that event fires while audio can still be in the local buffer.
        """
        if self._sealed:
            return True
        accepted = await super().write_audio_frame(frame)
        if not accepted:
            return False
        audio = getattr(frame, "audio", b"") or b""
        rate = int(getattr(frame, "sample_rate", 0) or 0)
        channels = int(getattr(frame, "num_channels", 1) or 1)
        if audio and rate > 0:
            duration = self.playout_clock.add_chunk(
                len(audio),
                rate,
                channels=channels,
                width=_PCM16_WIDTH,
            )
            if duration > 0 and self._owed_s > 0:
                self._owed_s = max(0.0, self._owed_s - duration)
            self._drain_gen += 1
            self._drain_sent_gen = None
        return True


class FernrufAsteriskTransport(AsteriskWebsocketTransport):
    """Asterisk websocket transport with a playout-aware output."""

    def __init__(
        self,
        websocket: WebSocket,
        params: FastAPIWebsocketParams | None = None,
        input_name: str | None = None,
        output_name: str | None = None,
    ):
        super().__init__(
            websocket=websocket,
            params=params,
            input_name=input_name,
            output_name=output_name,
        )
        self._output = FernrufAsteriskOutputTransport(
            self, self._client, self._params, name=self._output_name
        )
