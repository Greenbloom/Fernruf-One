"""Outbound monitor audio stays continuous when TTS arrives as a burst."""

from __future__ import annotations

from fernruf.monitor.hub import CallMonitor
from fernruf.monitor.pace import FRAME_MS, MONITOR_RATE, PlaybackPacer, to_s16_mono
from fernruf.monitor.protocol import unpack_audio


def _frame(ms: int = FRAME_MS) -> bytes:
    return b"\x01\x00" * int(MONITOR_RATE * ms / 1000)


def test_bursty_outbound_playback_is_continuous() -> None:
    now = [0.0]
    pacer = PlaybackPacer(clock=lambda: now[0])
    for _ in range(50):
        pacer.push(_frame(), sample_rate=MONITOR_RATE, channels=1)
    released: list[tuple[float, bytes]] = []
    for step in range(80):
        now[0] = step * (FRAME_MS / 1000)
        for chunk in pacer.take(now[0]):
            released.append((now[0], chunk))
    assert len(released) == 50
    at_start = sum(1 for stamp, _chunk in released if stamp == 0.0)
    assert 0 < at_start <= 3
    assert at_start < 50
    stamps = [stamp for stamp, _chunk in released]
    gaps = [right - left for left, right in zip(stamps, stamps[1:])]
    assert gaps
    assert max(gaps) <= (FRAME_MS / 1000) + 0.001
    assert stamps[-1] - stamps[0] >= 0.9
    assert all(len(chunk) == len(_frame()) for _stamp, chunk in released)


def test_both_legs_are_16k_mono() -> None:
    stereo = b"\x01\x00\x02\x00" * 160
    mono = to_s16_mono(stereo, 8000, 2)
    assert len(mono) == 640
    assert mono[0:2] == b"\x01\x00"


def test_outbound_tap_does_not_dump_a_burst() -> None:
    monitor = CallMonitor()
    monitor.begin("call-1", to_e164="+493000000000")
    sub = monitor.subscribe(listen=True, audio_queue_size=8)
    now = [0.0]
    for _ in range(50):
        monitor.offer_paced_outbound(
            "call-1",
            _frame(),
            sample_rate=MONITOR_RATE,
            channels=1,
            now=now[0],
        )
    first = sub.audio.qsize()
    assert 0 < first <= 3
    offered: list[float] = []
    for step in range(80):
        now[0] = step * (FRAME_MS / 1000)
        before = sub.audio.qsize()
        monitor.release_outbound("call-1", now=now[0])
        while sub.audio.qsize() > before:
            frame = unpack_audio(sub.audio.get_nowait())
            assert frame["direction"] == "outbound"
            assert frame["sample_rate"] == MONITOR_RATE
            assert frame["channels"] == 1
            offered.append(now[0])
            before = sub.audio.qsize()
    assert first <= 3
    assert len(offered) + first == 50
    assert offered[-1] - offered[0] >= 0.9
