"""One owner for monitor state, transcript lines, and audio copies.

Audio is copied only while a subscriber has opted in. A full queue drops the
frame. The call path never waits on a subscriber.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fernruf.monitor.pace import MONITOR_RATE, PlaybackPacer
from fernruf.monitor.protocol import lifecycle_message, mask_e164, pack_audio, transcript_message
from fernruf.monitor.state import CallLifecycle, next_state


@dataclass
class Subscription:
    events: asyncio.Queue[dict[str, Any]]
    audio: asyncio.Queue[bytes]
    listen: bool = False
    dropped: int = 0


@dataclass
class _CallView:
    state: CallLifecycle | None = None
    to_masked: str = "••••"
    outcome: str | None = None
    summary: dict[str, Any] | None = None
    started: float = field(default_factory=time.monotonic)


class CallMonitor:
    def __init__(self, *, audio_queue_size: int = 8) -> None:
        self._audio_queue_size = max(1, audio_queue_size)
        self._calls: dict[str, _CallView] = {}
        self._subs: list[Subscription] = []
        self._history: list[dict[str, Any]] = []
        self._outbound: dict[str, PlaybackPacer] = {}

    def begin(self, call_id: str, *, to_e164: str) -> bool:
        if call_id in self._calls:
            return False
        view = _CallView(to_masked=mask_e164(to_e164))
        self._calls[call_id] = view
        return self.advance(call_id, CallLifecycle.DIALING)

    def advance(
        self,
        call_id: str,
        state: CallLifecycle,
        *,
        outcome: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> bool:
        view = self._calls.get(call_id)
        if view is None:
            return False
        nxt = next_state(view.state, state)
        if nxt is None:
            return False
        view.state = nxt
        if outcome is not None:
            view.outcome = outcome
        if nxt is CallLifecycle.FINALIZED:
            view.summary = summary
        self._publish(
            lifecycle_message(
                call_id=call_id,
                state=nxt,
                at=datetime.now(UTC).isoformat(),
                to_masked=view.to_masked,
                outcome=view.outcome,
                summary=view.summary if nxt is CallLifecycle.FINALIZED else None,
            )
        )
        return True

    def note_transcript(
        self,
        call_id: str,
        *,
        speaker: str,
        text: str,
        t_ms: int,
        final: bool = True,
    ) -> None:
        text = text.strip()
        if call_id not in self._calls or speaker not in ("agent", "callee") or not text:
            return
        self._publish(
            transcript_message(
                call_id=call_id,
                speaker=speaker,
                text=text,
                t_ms=t_ms,
                final=final,
            )
        )

    def wants_audio(self) -> bool:
        return any(sub.listen for sub in self._subs)

    def offer_paced_outbound(
        self,
        call_id: str,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
        now: float | None = None,
    ) -> None:
        """Buffer outbound TTS and release it at realtime. Never blocks."""
        if not pcm:
            return
        pacer = self._outbound.get(call_id)
        if pacer is None:
            pacer = PlaybackPacer()
            self._outbound[call_id] = pacer
        pacer.push(pcm, sample_rate=sample_rate, channels=channels)
        self.release_outbound(call_id, now=now)

    def release_outbound(self, call_id: str, *, now: float | None = None) -> None:
        """Hand due outbound frames to listeners. A full queue still drops."""
        pacer = self._outbound.get(call_id)
        if pacer is None:
            return
        for chunk in pacer.take(now):
            self.offer_audio(
                call_id,
                direction="outbound",
                pcm=chunk,
                sample_rate=MONITOR_RATE,
                channels=1,
            )

    def offer_audio(
        self,
        call_id: str,
        *,
        direction: str,
        pcm: bytes,
        sample_rate: int,
        channels: int,
        pts_ms: int | None = None,
    ) -> int:
        """Queue one frame for each listening subscriber. Drop when the queue is full."""
        if not pcm:
            return 0
        listeners = [sub for sub in self._subs if sub.listen]
        if not listeners:
            return 0
        if pts_ms is None:
            view = self._calls.get(call_id)
            anchor = view.started if view is not None else time.monotonic()
            pts_ms = max(0, int((time.monotonic() - anchor) * 1000))
        frame = pack_audio(
            call_id=call_id,
            direction=direction,
            pcm=pcm,
            sample_rate=sample_rate,
            channels=channels,
            pts_ms=pts_ms,
        )
        dropped = 0
        for sub in listeners:
            try:
                sub.audio.put_nowait(frame)
            except asyncio.QueueFull:
                sub.dropped += 1
                dropped += 1
        return dropped

    def subscribe(
        self, *, listen: bool = False, audio_queue_size: int | None = None
    ) -> Subscription:
        size = self._audio_queue_size if audio_queue_size is None else max(1, audio_queue_size)
        sub = Subscription(
            events=asyncio.Queue(),
            audio=asyncio.Queue(maxsize=size),
            listen=listen,
        )
        self._subs.append(sub)
        for event in self._history:
            sub.events.put_nowait(event)
        return sub

    def set_listen(self, sub: Subscription, enabled: bool) -> None:
        sub.listen = bool(enabled)

    def unsubscribe(self, sub: Subscription) -> None:
        try:
            self._subs.remove(sub)
        except ValueError:
            return

    def _publish(self, event: dict[str, Any]) -> None:
        self._history.append(event)
        if len(self._history) > 2000:
            del self._history[: len(self._history) - 2000]
        for sub in self._subs:
            sub.events.put_nowait(event)
