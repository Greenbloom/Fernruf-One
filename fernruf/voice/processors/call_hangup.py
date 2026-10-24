"""Single hangup path once the call phase is ending.

``BotStoppedSpeaking`` means the pipeline finished queueing audio into the
output transport, not that the phone finished playing it, and not even that
``write_audio_frame`` has run. Pipecat writes that queue on another task.
Fast TTS (and a cache hit) can fill it in a fraction of the audio's duration.

When the output transport exposes a playout clock:

1. The wait starts once every TTS context of the final turn has stopped,
   including the pre-goodbye sentence and the goodbye. An earlier sentence's
   ``TTSStopped`` is not the goodbye.
2. Nothing is sealed until that audio has actually been written. Sealing at
   ``TTSStopped`` drops the burst still sitting in the output queue.
3. The deadline is the whole final turn: first write plus the sum of written
   durations, plus the margin (700 ms, ``FERNRUF_HANGUP_MARGIN_MS``). The
   running clock's tail is not enough once audio was written faster than real
   time. Capped at 20 s. Later chunks cannot move it later. Barge-in can.
4. ``QUEUE_DRAINED`` before that playout end is a drain of whatever Asterisk
   had queued so far. It does not hang up. A drain after the playout end
   still waits the margin. If no drain arrives, hang up at the deadline.
5. Audio after the burst has been written is dropped, so silence cannot bump
   the clock.

Without a clock (unit tests, stock transport), keep the previous path:
``BotStoppedSpeaking`` plus the margin, with a fallback of goodbye audio
length + 1.5 s from ``TTSStarted``.

The hangup hook is the existing ARI teardown.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InputTransportMessageFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.playout import PlayoutClock

LOG = logging.getLogger("fernruf.voice.end_call")

_FALLBACK_PAD_S = 1.5
_PLAYOUT_CAP_S = 20.0
_PLAYOUT_POLL_S = 0.05


def _norm(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return t.strip(" .,!?;:")


def _phrase_in(expect: str, heard: str) -> bool:
    e = _norm(expect)
    h = _norm(heard)
    if not e or not h:
        return False
    if e in h or h in e:
        return True
    return len(h) >= 8 and e.endswith(h)


def _pcm_seconds(frame: TTSAudioRawFrame) -> float:
    rate = getattr(frame, "sample_rate", None) or 16000
    audio = getattr(frame, "audio", b"") or b""
    if rate <= 0 or not audio:
        return 0.0
    return len(audio) / (2 * rate)


class CallHangup(FrameProcessor):
    """Speak an optional goodbye, then hang up when the phone has heard it."""

    def __init__(
        self,
        *,
        hooks: M2CallHooks,
        session: M2CallSession,
        settings: Settings,
        playout: PlayoutClock | None = None,
        request_queue_drained: Callable[[], Awaitable[bool]] | None = None,
        accept_queue_drained: Callable[[], bool] | None = None,
        seal_playout: Callable[[], None] | None = None,
        output_pending: Callable[[], bool] | None = None,
        note_queued_audio: Callable[[float], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._hooks = hooks
        self._session = session
        self._settings = settings
        self._playout = playout
        self._request_queue_drained = request_queue_drained
        self._accept_queue_drained = accept_queue_drained
        self._seal_playout = seal_playout
        self._output_pending = output_pending
        self._note_queued_audio = note_queued_audio
        self._armed = False
        self._done = False
        self._need_speak = False
        self._speak_pushed = False
        self._wait_phrase = ""
        self._heard = ""
        self._phrase_seen = False
        self._bot_speaking = False
        self._playback_stopped = False
        self._audio_s = 0.0
        self._tts_started_at: float | None = None
        self._utt_started_at: float | None = None
        self._utt_audio_s = 0.0
        self._margin_task: asyncio.Task | None = None
        self._fallback_task: asyncio.Task | None = None
        self._cap_task: asyncio.Task | None = None
        self._drain_asked = False
        self._queue_drained = False
        self._tts_open = False
        self._tts_depth = 0
        self._ctx_seq = 0
        self._ctx_order: list[str] = []
        self._ctx_text: dict[str, str] = {}
        self._nonmatching_tts = False
        self._goodbye_ctx_done = False
        self._goodbye_tts_done = False
        self._goodbye_sealed = False
        self._hard_deadline: float | None = None
        self._playout_end: float | None = None
        self._deadline_clock_end = 0.0
        self._drain_mono: float | None = None
        self._dropped_late_audio = False
        self._final_turn_logged = False

    def _create_task(self, coro, name: str) -> asyncio.Task:
        tm = getattr(self, "_task_manager", None)
        if tm:
            return self.create_task(coro, name=name)
        return asyncio.get_running_loop().create_task(coro)

    def _cancel(self, task: asyncio.Task | None) -> None:
        if task and not task.done():
            task.cancel()

    def _estimate_s(self) -> float:
        if self._audio_s > 0:
            return self._audio_s
        text = self._wait_phrase or self._session.phase.spoken_text or self._session.phase.goodbye_tts
        if text:
            return max(len(text) / 14.0, 0.3)
        return 0.3

    def _schedule_margin(self) -> None:
        if self._done or self._playout is not None:
            return
        self._cancel(self._margin_task)
        margin_s = self._settings.hangup_margin_s

        async def _wait() -> None:
            try:
                await asyncio.sleep(margin_s)
            except asyncio.CancelledError:
                return
            await self._execute_hangup()

        self._margin_task = self._create_task(_wait(), "hangup_after_playback")

    def _schedule_fallback(self) -> None:
        if self._done or self._playout is not None:
            return
        if self._wait_phrase and not self._phrase_seen:
            return
        start = self._tts_started_at
        if start is None:
            start = time.monotonic()
            self._tts_started_at = start
        delay = self._estimate_s() + _FALLBACK_PAD_S
        remaining = max(0.05, delay - (time.monotonic() - start))
        self._cancel(self._fallback_task)

        async def _wait() -> None:
            try:
                await asyncio.sleep(remaining)
            except asyncio.CancelledError:
                return
            if self._wait_phrase and not self._phrase_seen:
                return
            LOG.warning(
                "Hangup fallback (audio %.2fs + %.1fs); forcing hangup",
                self._estimate_s(),
                _FALLBACK_PAD_S,
            )
            await self._execute_hangup()

        self._fallback_task = self._create_task(_wait(), "hangup_fallback")

    def _start_playout_wait(self) -> None:
        """One loop, started once the goodbye has been written."""
        if self._done or self._playout is None:
            return
        if self._margin_task and not self._margin_task.done():
            return
        self._margin_task = self._create_task(self._wait_for_playout(), "hangup_playout")

    async def _maybe_request_drain(self) -> None:
        if self._request_queue_drained is None:
            return
        try:
            sent = await self._request_queue_drained()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.warning("REPORT_QUEUE_DRAINED failed", exc_info=True)
            return
        if sent:
            self._drain_asked = True

    def _ensure_playout_cap(self) -> None:
        if self._cap_task is not None or self._playout is None or self._done:
            return

        async def _wait() -> None:
            try:
                await asyncio.sleep(_PLAYOUT_CAP_S)
            except asyncio.CancelledError:
                return
            if not self._done:
                LOG.warning("Playout wait hit %.0fs cap; hanging up", _PLAYOUT_CAP_S)
                await self._execute_hangup()

        self._cap_task = self._create_task(_wait(), "hangup_playout_cap")

    def _margin_s(self) -> float:
        return self._settings.hangup_margin_s

    def _output_still_pending(self) -> bool:
        fn = self._output_pending
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            LOG.warning("output_pending failed", exc_info=True)
            return False

    def _open_tts(self, context_id: str | None) -> None:
        self._tts_depth += 1
        self._tts_open = True
        cid = context_id or f"anon-{self._ctx_seq}"
        self._ctx_seq += 1
        self._ctx_order.append(cid)
        self._ctx_text.setdefault(cid, "")

    def _append_tts_text(self, frame: TTSTextFrame) -> None:
        text = frame.text or ""
        cid = getattr(frame, "context_id", None)
        if cid and cid in self._ctx_text:
            self._ctx_text[cid] += text
            return
        if self._ctx_order:
            self._ctx_text[self._ctx_order[-1]] += text

    def _close_tts(self, context_id: str | None) -> tuple[str, bool]:
        """Return the closed context's text, and whether a context was open."""
        self._tts_depth = max(0, self._tts_depth - 1)
        self._tts_open = self._tts_depth > 0
        cid = context_id if context_id and context_id in self._ctx_text else None
        if cid is None and self._ctx_order:
            cid = self._ctx_order[-1]
        if cid is None:
            return "", False
        text = self._ctx_text.pop(cid, "")
        if cid in self._ctx_order:
            self._ctx_order.remove(cid)
        return text, True

    def _freeze_deadline(self) -> None:
        """Snapshot first-write + full-turn duration + margin. Later audio cannot extend it."""
        now = time.monotonic()
        margin = self._margin_s()
        clock = self._playout
        audio_end = clock.goodbye_audio_end() if clock is not None else now
        self._playout_end = audio_end
        self._hard_deadline = min(audio_end + margin, now + _PLAYOUT_CAP_S)
        self._deadline_clock_end = clock.end if clock is not None else 0.0

    def _pull_deadline_earlier(self) -> None:
        """Barge-in flushes the clock and may shorten the wait. Nothing lengthens it."""
        clock = self._playout
        if clock is None or self._hard_deadline is None:
            return
        if clock.end + 0.02 >= self._deadline_clock_end and clock.end > 0:
            return
        margin = self._margin_s()
        now = time.monotonic()
        audio_end = clock.goodbye_audio_end()
        pulled = min(audio_end + margin, now + _PLAYOUT_CAP_S)
        self._deadline_clock_end = clock.end
        if pulled < self._hard_deadline:
            self._hard_deadline = pulled
            self._playout_end = audio_end

    def _log_final_turn(self) -> None:
        if self._final_turn_logged:
            return
        self._final_turn_logged = True
        clock = self._playout
        now = time.monotonic()
        total_ms = (clock.utterance_total * 1000) if clock is not None else 0.0
        first = clock.utterance_first if clock is not None else None
        end = self._playout_end if self._playout_end is not None else now
        end_from_first = ((end - first) * 1000) if first is not None else 0.0
        deadline = self._hard_deadline if self._hard_deadline is not None else end
        LOG.info(
            "Final-turn audio %.0fms written; playout clock end +%.0fms from first write "
            "(in %.0fms); fallback in %.0fms",
            total_ms,
            end_from_first,
            max(0.0, end - now) * 1000,
            max(0.0, deadline - now) * 1000,
        )

    def _seal_now(self) -> None:
        """Drop anything written after this, and freeze the final-turn deadline."""
        if self._goodbye_sealed or self._done or self._playout is None:
            return
        self._goodbye_sealed = True
        if self._seal_playout is not None:
            try:
                self._seal_playout()
            except Exception:
                LOG.warning("Failed to seal playout after goodbye", exc_info=True)
        self._freeze_deadline()
        self._log_final_turn()

    def _mark_goodbye_written(self) -> None:
        """Final-turn TTS has stopped. Seal only once its audio is written."""
        if self._goodbye_tts_done or self._done or self._playout is None:
            return
        self._goodbye_tts_done = True
        if self._goodbye_sealed:
            return
        if self._output_still_pending():
            LOG.info("Final-turn audio still writing; not sealing yet")
            self._start_playout_wait()
            return
        self._seal_now()
        self._start_playout_wait()

    async def _wait_for_playout(self) -> None:
        margin = self._margin_s()
        clock = self._playout
        assert clock is not None
        early_logged = False
        try:
            while not self._done:
                if not self._goodbye_sealed:
                    if self._output_still_pending():
                        await asyncio.sleep(_PLAYOUT_POLL_S)
                        continue
                    self._seal_now()
                    if self._done:
                        return
                self._pull_deadline_earlier()
                now = time.monotonic()
                deadline = self._hard_deadline if self._hard_deadline is not None else now
                play_end = self._playout_end if self._playout_end is not None else deadline
                # A drain of a partial queue, or a drain that arrives while the
                # phone still has the burst to play, must not hang up early.
                # Once the summed audio has reached real time, wait the margin
                # (and never less than the frozen deadline).
                if self._queue_drained and now + 1e-4 >= play_end:
                    LOG.info("Asterisk queue drained; hangup in %.0fms", margin * 1000)
                    target = max(deadline, now + margin)
                    delay = target - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if self._done:
                        return
                    await self._execute_hangup()
                    return
                if now >= deadline:
                    LOG.info("Playout deadline reached; hanging up")
                    await self._execute_hangup()
                    return
                if self._queue_drained and not early_logged:
                    LOG.info(
                        "QUEUE_DRAINED ignored; final-turn playout not finished (%.0fms left)",
                        max(0.0, play_end - now) * 1000,
                    )
                    early_logged = True
                await self._maybe_request_drain()
                slice_s = min(
                    _PLAYOUT_POLL_S,
                    max(0.01, deadline - time.monotonic()),
                )
                await asyncio.sleep(slice_s)
        except asyncio.CancelledError:
            return

    async def arm(self) -> None:
        if self._done or self._armed or not self._session.phase.is_ending:
            return
        self._armed = True
        self._ensure_playout_cap()
        phrase = self._session.phase.goodbye_tts.strip()
        if phrase:
            self._wait_phrase = phrase
            self._phrase_seen = False
            self._heard = ""
            self._need_speak = True
            return
        if self._session.phase.await_playback:
            # The goodbye is the utterance already spoken. BotStopped may
            # have passed before the phase flipped to ending.
            if self._tts_started_at is None and self._utt_started_at is not None:
                self._tts_started_at = self._utt_started_at
                self._audio_s = self._utt_audio_s
            if self._playout is not None:
                if self._playback_stopped and not self._tts_open:
                    self._mark_goodbye_written()
                return
            if self._playback_stopped:
                self._schedule_margin()
            self._schedule_fallback()
            return
        if self._playout is not None:
            self._mark_goodbye_written()
            return
        self._tts_started_at = time.monotonic()
        self._schedule_margin()
        self._schedule_fallback()

    def _note_tts_text(self, text: str) -> None:
        if not self._armed or not text:
            return
        self._heard += text
        if self._wait_phrase and not self._phrase_seen and _phrase_in(self._wait_phrase, self._heard):
            self._phrase_seen = True
            if self._utt_started_at is not None:
                self._tts_started_at = self._utt_started_at
                self._audio_s = self._utt_audio_s
            self._schedule_fallback()

    def _hangup_timing(self) -> str:
        clock = self._playout
        if clock is None or clock.utterance_first is None:
            return ""
        first = clock.utterance_first
        now = time.monotonic()
        end = self._playout_end if self._playout_end is not None else first
        if self._drain_mono is None:
            drain = "n/a"
        else:
            drain = f"{(self._drain_mono - first) * 1000:.0f}ms"
        return (
            f"; final-turn audio {clock.utterance_total * 1000:.0f}ms written, "
            f"playout clock end +{(end - first) * 1000:.0f}ms, "
            f"drain +{drain}, "
            f"hangup +{(now - first) * 1000:.0f}ms from first write"
        )

    async def _execute_hangup(self) -> None:
        if self._done or not self._session.phase.is_ending:
            return
        self._done = True
        current = asyncio.current_task()
        for task in (self._margin_task, self._fallback_task, self._cap_task):
            if task is not None and task is not current:
                self._cancel(task)
        self._margin_task = None
        self._fallback_task = None
        self._cap_task = None
        reason = self._session.phase.hangup_reason or "end_call"
        LOG.info("end_call: hanging up (%s)%s", reason[:120], self._hangup_timing())
        try:
            await self._hooks.hangup()
        finally:
            self._session.phase.mark_hung_up()
            if self._hooks.request_pipeline_shutdown:
                await self._hooks.request_pipeline_shutdown()
            await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)

    def _note_queue_drained(self, frame: Frame) -> None:
        if not isinstance(frame, InputTransportMessageFrame):
            return
        message = frame.message if isinstance(getattr(frame, "message", None), dict) else {}
        if message.get("event") != "QUEUE_DRAINED":
            return
        if self._queue_drained:
            return
        now = time.monotonic()
        if not self._goodbye_sealed or not self._drain_asked:
            if self._drain_mono is None:
                self._drain_mono = now
            LOG.info(
                "QUEUE_DRAINED ignored (sealed=%s report_sent=%s); final-turn audio still writing",
                self._goodbye_sealed,
                self._drain_asked,
            )
            return
        accept = self._accept_queue_drained
        if accept is not None and not accept():
            LOG.info("QUEUE_DRAINED ignored; it does not match the report still in effect")
            return
        self._drain_mono = now
        play_end = self._playout_end
        if play_end is not None and now + 0.01 < play_end:
            LOG.info(
                "QUEUE_DRAINED ignored; final-turn playout not finished (%.0fms left)",
                (play_end - now) * 1000,
            )
            self._queue_drained = True
            return
        LOG.info("QUEUE_DRAINED accepted")
        self._queue_drained = True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if self._session.phase.is_ending and not self._armed and not self._done:
            await self.arm()
        if self._need_speak and not self._done:
            self._need_speak = False
            self._speak_pushed = True
            phrase = self._wait_phrase
            LOG.info("end_call: speaking goodbye via TTS (%s chars)", len(phrase))
            await self.push_frame(
                TTSSpeakFrame(text=phrase, append_to_context=False),
                FrameDirection.UPSTREAM,
            )

        self._note_queue_drained(frame)

        if isinstance(frame, (TTSStartedFrame, BotStartedSpeakingFrame)):
            self._bot_speaking = True
            self._playback_stopped = False
            if isinstance(frame, TTSStartedFrame):
                self._open_tts(getattr(frame, "context_id", None))
                self._utt_started_at = time.monotonic()
                self._utt_audio_s = 0.0
                self._heard = ""
                if self._armed and not self._wait_phrase:
                    if self._tts_started_at is None:
                        self._tts_started_at = self._utt_started_at
                    self._schedule_fallback()

        if isinstance(frame, TTSAudioRawFrame) and self._armed and not self._goodbye_sealed:
            seconds = _pcm_seconds(frame)
            self._utt_audio_s += seconds
            if self._note_queued_audio is not None and seconds > 0:
                try:
                    self._note_queued_audio(seconds)
                except Exception:
                    LOG.warning("note_queued_audio failed", exc_info=True)
            if not self._wait_phrase or self._phrase_seen:
                self._audio_s += seconds
                if self._tts_started_at is None:
                    self._tts_started_at = self._utt_started_at or time.monotonic()
                self._schedule_fallback()

        if isinstance(frame, TTSTextFrame):
            self._append_tts_text(frame)
            self._note_tts_text(frame.text or "")

        if isinstance(frame, TTSStoppedFrame):
            self._bot_speaking = False
            text, closed = self._close_tts(getattr(frame, "context_id", None))
            if (
                closed
                and self._speak_pushed
                and self._wait_phrase
                and not self._goodbye_tts_done
            ):
                if text and _phrase_in(self._wait_phrase, text):
                    self._goodbye_ctx_done = True
                    self._phrase_seen = True
                elif text.strip():
                    self._nonmatching_tts = True
                elif not self._nonmatching_tts:
                    # No text in this context (unit tests, or a TTS that does
                    # not push text). The in-flight sentence that is not the
                    # goodbye does push text, so it does not take this path.
                    self._goodbye_ctx_done = True
                    self._phrase_seen = True
                if self._phrase_seen:
                    self._goodbye_ctx_done = True
            if (
                self._playout is not None
                and self._goodbye_ctx_done
                and self._tts_depth == 0
                and not self._goodbye_tts_done
            ):
                self._mark_goodbye_written()
            elif (
                self._armed
                and self._playout is not None
                and not self._wait_phrase
                and self._session.phase.await_playback
                and not self._goodbye_sealed
                and self._tts_depth == 0
            ):
                self._mark_goodbye_written()
            if self._armed and (not self._wait_phrase or self._phrase_seen):
                self._schedule_fallback()

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._playback_stopped = True
            self._session.last_bot_stopped_speaking_mono = time.monotonic()
            if self._armed and not self._done and (not self._wait_phrase or self._phrase_seen):
                if self._playout is not None:
                    # Previous-utterance stop can arrive after the goodbye TTS
                    # has started and before its audio exists. Wait for that
                    # TTS to stop. A stop with no TTS in flight is the write.
                    waiting_for_goodbye_tts = (
                        self._speak_pushed and self._wait_phrase and not self._goodbye_tts_done
                    )
                    if not self._goodbye_sealed and not self._tts_open and not waiting_for_goodbye_tts:
                        self._mark_goodbye_written()
                else:
                    LOG.info(
                        "Bot stopped speaking; hangup in %.0fms",
                        self._settings.hangup_margin_s * 1000,
                    )
                    self._schedule_margin()

        if self._goodbye_sealed and isinstance(frame, TTSAudioRawFrame):
            if not self._dropped_late_audio:
                self._dropped_late_audio = True
                LOG.info("Dropping audio written after the goodbye")
            await super().process_frame(frame, direction)
            return

        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
