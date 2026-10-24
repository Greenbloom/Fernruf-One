"""Hangup waits for phone playout, not for the audio write to finish."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TextType,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat_asterisk.frames import AsteriskCommandFrame
from pipecat_asterisk.transport.transport import AsteriskWebsocketOutputTransport

from fernruf.settings import Settings
from fernruf.voice.asterisk_transport import FernrufAsteriskOutputTransport
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.playout import PlayoutClock
from fernruf.voice.processors.call_hangup import CallHangup

_RATE = 16000


class _Flow:
    def __init__(self) -> None:
        self._local_buffer_size = 0

    def __call__(self, chunk: bytes) -> None:
        self._local_buffer_size += len(chunk)

    def drop_buffer(self) -> None:
        self._local_buffer_size = 0


class _Client:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, data: object) -> None:
        self.sent.append(data)


def _pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * _RATE)


def _output() -> tuple[FernrufAsteriskOutputTransport, _Flow, _Client]:
    params = FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True)
    client = _Client()
    out = FernrufAsteriskOutputTransport(object(), client, params)
    flow = _Flow()
    out._flow_controller = flow

    async def serialize(frame: Frame):
        if isinstance(frame, OutputAudioRawFrame):
            return bytes(frame.audio)
        if isinstance(frame, AsteriskCommandFrame):
            return frame.cmd
        return None

    out.serialize = serialize  # type: ignore[method-assign]
    return out, flow, client


def _hangup(
    *,
    session: M2CallSession,
    clock: PlayoutClock,
    margin_s: float,
    request=None,
    accept=None,
    seal=None,
    output_pending=None,
    note_queued=None,
) -> tuple[CallHangup, AsyncMock]:
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=margin_s,
    )
    called = AsyncMock()
    proc = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=called, session=session),
        session=session,
        settings=settings,
        playout=clock,
        request_queue_drained=request,
        accept_queue_drained=accept,
        seal_playout=seal,
        output_pending=output_pending,
        note_queued_audio=note_queued,
    )
    proc.push_frame = AsyncMock()  # type: ignore[method-assign]
    return proc, called


async def _arm_goodbye(proc: CallHangup) -> None:
    """Arm on a stop that arrives before the goodbye, then finish that TTS write."""
    await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
    await proc.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)


def test_playout_clock_sums_instant_chunks() -> None:
    clock = PlayoutClock()
    started = time.monotonic()
    for seconds in (0.2, 0.2, 0.3):
        clock.add_chunk(len(_pcm(seconds)), _RATE, channels=1, width=2)
    assert abs(clock.remaining() - 0.7) < 0.03
    assert clock.end >= started + 0.7 - 0.03
    clock.reset()
    assert clock.remaining() == 0.0


def test_turn_span_includes_audio_before_a_later_tts_context() -> None:
    """A goodbye TTS start must not zero audio already written this turn."""
    clock = PlayoutClock()
    clock.add_chunk(len(_pcm(1.0)), _RATE)
    first = clock.utterance_first
    clock.begin_utterance()
    clock.add_chunk(len(_pcm(1.5)), _RATE)
    assert clock.utterance_first == first
    assert abs(clock.utterance_total - 2.5) < 0.01
    assert abs(clock.goodbye_audio_end() - (first + 2.5)) < 0.03


def test_output_counts_chunks_and_resets_on_barge_in() -> None:
    async def run() -> None:
        out, flow, client = _output()
        for seconds in (0.2, 0.2):
            ok = await out.write_audio_frame(
                OutputAudioRawFrame(audio=_pcm(seconds), sample_rate=_RATE, num_channels=1)
            )
            assert ok
        assert abs(out.playout_clock.remaining() - 0.4) < 0.03
        assert flow._local_buffer_size > 0

        assert await out.request_queue_drained() is False
        assert client.sent == []

        flow._local_buffer_size = 0
        assert await out.request_queue_drained() is True
        assert client.sent == ["REPORT_QUEUE_DRAINED"]
        assert out.accept_queue_drained() is True

        await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(0.2), sample_rate=_RATE, num_channels=1)
        )
        assert out.accept_queue_drained() is False
        assert out.playout_clock.remaining() > 0.1

        # Parent process_frame needs a live websocket and task manager; the
        # clock reset happens before that, and FLUSH_MEDIA is the parent's job.
        parent = AsyncMock()

        async def _super(self, frame, direction):
            await parent(frame, direction)

        with patch.object(AsteriskWebsocketOutputTransport, "process_frame", _super):
            await out.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        parent.assert_awaited()
        assert out.playout_clock.remaining() == 0.0
        assert out.accept_queue_drained() is False

    asyncio.run(run())


def test_instant_sentences_hang_up_after_summed_playout_plus_margin() -> None:
    """Several sentences plus a cached goodbye, written instantly.

    The old path hung up 400 ms after BotStoppedSpeaking. Playout is the sum
    of those chunks plus the 400 ms margin.
    """

    async def run() -> None:
        out, _flow, _client = _output()
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )
        durations = (0.2, 0.2, 0.2)
        audio_s = sum(durations)
        margin_s = 0.4
        written_at = time.monotonic()
        for seconds in durations:
            ok = await out.write_audio_frame(
                OutputAudioRawFrame(audio=_pcm(seconds), sample_rate=_RATE, num_channels=1)
            )
            assert ok
        proc, called = _hangup(
            session=session,
            clock=out.playout_clock,
            margin_s=margin_s,
            request=out.request_queue_drained,
            accept=out.accept_queue_drained,
        )
        await _arm_goodbye(proc)
        t0 = time.monotonic()
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.5)
        assert called.await_count == 0
        target = written_at + audio_s + margin_s
        await asyncio.sleep(max(0.05, target - time.monotonic() + 0.2))
        assert called.await_count == 1
        assert time.monotonic() >= target - 0.08
        assert t0 > 0

    asyncio.run(run())


def test_early_queue_drained_cannot_cut_the_final_turn() -> None:
    """A drain while seconds of audio are still unplayed does not hang up.

    The phone has not heard the burst just because Asterisk emptied the
    partial queue it had. Hangup is the summed playout end plus the margin.
    """

    async def run() -> None:
        clock = PlayoutClock()
        clock.add_chunk(len(_pcm(1.5)), _RATE)
        first = clock.utterance_first
        assert first is not None
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )

        async def request() -> bool:
            return True

        margin_s = 0.25
        proc, called = _hangup(
            session=session,
            clock=clock,
            margin_s=margin_s,
            request=request,
            accept=lambda: True,
        )
        await _arm_goodbye(proc)
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.12)
        await proc.process_frame(
            InputTransportMessageFrame(message={"event": "QUEUE_DRAINED"}),
            FrameDirection.DOWNSTREAM,
        )
        earliest = first + 1.5 + margin_s
        while time.monotonic() < earliest - 0.08:
            assert called.await_count == 0
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.45)
        assert called.await_count == 1
        assert time.monotonic() >= earliest - 0.05

    asyncio.run(run())


def test_barge_in_during_wait_uses_the_reset_clock() -> None:
    async def run() -> None:
        clock = PlayoutClock()
        clock.add_chunk(len(_pcm(2.0)), _RATE)
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )
        proc, called = _hangup(session=session, clock=clock, margin_s=0.25)
        await _arm_goodbye(proc)
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        clock.reset()
        clock.add_chunk(len(_pcm(0.2)), _RATE)
        reset_at = time.monotonic()
        await asyncio.sleep(0.3)
        assert called.await_count == 0
        await asyncio.sleep(0.35)
        assert called.await_count == 1
        assert time.monotonic() - reset_at < 1.0

    asyncio.run(run())


def test_repeat_drains_and_late_audio_hang_up_on_the_first_valid_drain() -> None:
    """A drain before the goodbye write is ignored. After that write, one
    QUEUE_DRAINED is remembered and does not add a second wait. It also does
    not hang up before the summed playout end plus the margin. Later drains
    and silence cannot move the clock, even when accept() would fail again.
    """

    async def run() -> None:
        out, flow, client = _output()
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(0.4), sample_rate=_RATE, num_channels=1)
        )
        looks = {"n": 0}

        def accept_once() -> bool:
            looks["n"] += 1
            return looks["n"] == 1

        proc, called = _hangup(
            session=session,
            clock=out.playout_clock,
            margin_s=0.4,
            request=out.request_queue_drained,
            accept=accept_once,
            seal=out.seal_playout,
        )
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        flow._local_buffer_size = 0
        await proc.process_frame(
            InputTransportMessageFrame(message={"event": "QUEUE_DRAINED"}),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.45)
        assert called.await_count == 0
        assert looks["n"] == 0

        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(0.35), sample_rate=_RATE, num_channels=1)
        )
        sealed_end = out.playout_clock.end
        flow._local_buffer_size = 0
        await proc.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert out._sealed is True

        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(2.0), sample_rate=_RATE, num_channels=1)
        )
        assert out.playout_clock.end == sealed_end
        late = TTSAudioRawFrame(audio=_pcm(1.0), sample_rate=_RATE, num_channels=1)
        await proc.process_frame(late, FrameDirection.DOWNSTREAM)
        forwarded = [
            call.args[0]
            for call in proc.push_frame.await_args_list
            if call.args and isinstance(call.args[0], TTSAudioRawFrame)
        ]
        assert late not in forwarded
        assert out.playout_clock.end == sealed_end

        for _ in range(20):
            if "REPORT_QUEUE_DRAINED" in client.sent:
                break
            await asyncio.sleep(0.02)
        assert "REPORT_QUEUE_DRAINED" in client.sent

        drained_at = time.monotonic()
        await proc.process_frame(
            InputTransportMessageFrame(message={"event": "QUEUE_DRAINED"}),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            InputTransportMessageFrame(message={"event": "QUEUE_DRAINED"}),
            FrameDirection.DOWNSTREAM,
        )
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(5.0), sample_rate=_RATE, num_channels=1)
        )
        assert out.playout_clock.end == sealed_end
        earliest = sealed_end + 0.4
        while time.monotonic() < earliest - 0.06:
            assert called.await_count == 0
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.4)
        assert called.await_count == 1
        assert time.monotonic() >= earliest - 0.05
        assert looks["n"] == 1
        assert drained_at > 0

    asyncio.run(run())


def test_audio_after_the_goodbye_cannot_push_the_hangup_deadline() -> None:
    """A chunk that lands on the clock after the goodbye write cannot move hangup later."""

    async def run() -> None:
        out, _flow, _client = _output()
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(0.8), sample_rate=_RATE, num_channels=1)
        )
        proc, called = _hangup(
            session=session,
            clock=out.playout_clock,
            margin_s=0.3,
            seal=out.seal_playout,
        )
        await proc.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await proc.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        frozen = proc._hard_deadline
        assert frozen is not None
        out.playout_clock.add_chunk(len(_pcm(5.0)), _RATE)
        assert out.playout_clock.end > frozen
        slack = frozen - time.monotonic()
        await asyncio.sleep(max(0.0, slack - 0.15))
        assert called.await_count == 0
        await asyncio.sleep(0.4)
        assert called.await_count == 1
        assert time.monotonic() < frozen + 0.35

    asyncio.run(run())


def test_streamed_goodbye_without_drain_waits_out_the_whole_utterance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Chunks that arrive over ~1s must not hang up on the short local tail.

    No QUEUE_DRAINED arrives. Hangup is no earlier than the first goodbye
    chunk plus the summed duration plus the margin.
    """

    async def run() -> None:
        out, flow, client = _output()
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Vielen Dank, auf Wiederhören.",
            await_playback=True,
        )
        margin_s = 0.35
        proc, called = _hangup(
            session=session,
            clock=out.playout_clock,
            margin_s=margin_s,
            request=out.request_queue_drained,
            accept=out.accept_queue_drained,
            seal=out.seal_playout,
        )
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        durations = (0.25, 0.25, 0.25, 0.25)
        for seconds in durations:
            assert await out.write_audio_frame(
                OutputAudioRawFrame(audio=_pcm(seconds), sample_rate=_RATE, num_channels=1)
            )
            await asyncio.sleep(0.25)
        flow._local_buffer_size = 0
        await proc.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        first = out.playout_clock.utterance_first
        total = out.playout_clock.utterance_total
        assert first is not None
        assert abs(total - sum(durations)) < 0.03
        earliest = first + total + margin_s
        while time.monotonic() < earliest - 0.08:
            assert called.await_count == 0
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.45)
        assert called.await_count == 1
        assert time.monotonic() >= earliest - 0.05
        assert "REPORT_QUEUE_DRAINED" in client.sent

    caplog.set_level(logging.INFO)
    asyncio.run(run())
    assert any("Sent REPORT_QUEUE_DRAINED" in r.message for r in caplog.records)
    assert any("Playout deadline reached" in r.message for r in caplog.records)
    assert not any("QUEUE_DRAINED accepted" in r.message for r in caplog.records)


def test_fast_tts_burst_plays_the_whole_final_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """4s of final-turn audio is written in ~200ms. Hangup waits it out.

    Mirrors a paid Fish s2.1-pro turn: "Vielen Dank" is already on the wire,
    then "Ich werde …" and a cache-hit "Auf Wiederhören" land as a burst.
    An early QUEUE_DRAINED of the partial queue must not hang up, and the
    pre-goodbye sentence's TTSStopped must not seal the goodbye.
    """

    async def run() -> None:
        out, flow, _client = _output()
        session = M2CallSession()
        session.phase.begin_ending(
            reason="done",
            goodbye="Auf Wiederhören.",
            await_playback=True,
        )
        margin_s = 0.2
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(1.0), sample_rate=_RATE, num_channels=1)
        )
        first = out.playout_clock.utterance_first
        assert first is not None
        proc, called = _hangup(
            session=session,
            clock=out.playout_clock,
            margin_s=margin_s,
            request=out.request_queue_drained,
            accept=out.accept_queue_drained,
            seal=out.seal_playout,
            output_pending=out.output_audio_pending,
            note_queued=out.note_audio_queued,
        )
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSStartedFrame(context_id="sentence"), FrameDirection.DOWNSTREAM
        )
        await proc.process_frame(
            TTSTextFrame(
                text="Ich werde Alex Example informieren.",
                text_type=TextType.SENTENCE,
                context_id="sentence",
            ),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSStoppedFrame(context_id="sentence"), FrameDirection.DOWNSTREAM
        )
        assert out._sealed is False
        assert proc._goodbye_tts_done is False

        await proc.process_frame(TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSTextFrame(
                text="Auf Wiederhören.",
                text_type=TextType.SENTENCE,
                context_id="bye",
            ),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSAudioRawFrame(audio=_pcm(1.5), sample_rate=_RATE, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TTSAudioRawFrame(audio=_pcm(1.5), sample_rate=_RATE, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(TTSStoppedFrame(context_id="bye"), FrameDirection.DOWNSTREAM)
        assert out._sealed is False
        assert called.await_count == 0

        flow._local_buffer_size = 0
        await proc.process_frame(
            InputTransportMessageFrame(message={"event": "QUEUE_DRAINED"}),
            FrameDirection.DOWNSTREAM,
        )
        assert called.await_count == 0

        burst_at = time.monotonic()
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(1.5), sample_rate=_RATE, num_channels=1)
        )
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(1.5), sample_rate=_RATE, num_channels=1)
        )
        assert time.monotonic() - burst_at < 0.2
        assert abs(out.playout_clock.utterance_total - 4.0) < 0.05

        for _ in range(30):
            if out._sealed:
                break
            await asyncio.sleep(0.02)
        assert out._sealed is True
        # A chunk that shows up only after the seal must not stretch the wait.
        sealed_total = out.playout_clock.utterance_total
        assert await out.write_audio_frame(
            OutputAudioRawFrame(audio=_pcm(5.0), sample_rate=_RATE, num_channels=1)
        )
        assert out.playout_clock.utterance_total == sealed_total

        earliest = first + out.playout_clock.utterance_total + margin_s
        while time.monotonic() < earliest - 0.08:
            assert called.await_count == 0, "hung up before the final turn finished playing"
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        assert called.await_count == 1
        assert time.monotonic() >= earliest - 0.05

    caplog.set_level(logging.INFO)
    asyncio.run(run())
    assert any("Final-turn audio" in r.message and "4000ms written" in r.message for r in caplog.records)
    assert any("playout clock end" in r.message for r in caplog.records)
    assert any("QUEUE_DRAINED ignored" in r.message for r in caplog.records)
    hangup_logs = [r.message for r in caplog.records if r.message.startswith("end_call: hanging up")]
    assert len(hangup_logs) == 1
    assert "final-turn audio" in hangup_logs[0]
    assert "playout clock end" in hangup_logs[0]
    assert "drain +" in hangup_logs[0]
    assert "hangup +" in hangup_logs[0]
    assert "n/a" not in hangup_logs[0]
