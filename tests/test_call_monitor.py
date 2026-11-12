"""Local monitor: lifecycle, transcript, audio, and a subscriber that does not read."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

import pytest
import websockets
from pipecat.frames.frames import InputAudioRawFrame, TTSAudioRawFrame
from tests.m3_support import service_settings

from fernruf.desktop.prefs import load_listen_pref, save_listen_pref
from fernruf.monitor.protocol import unpack_audio
from fernruf.monitor.server import start_monitor_server
from fernruf.monitor.state import CallLifecycle, next_state
from fernruf.quiet_hours import BERLIN
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallManager
from fernruf.service.mcp_app import create_app
from fernruf.service.security import BindError
from fernruf.voice.processors.audio_tap import note_frame

TASK = {"goal": "Frag, ob Dienstag passt.", "questions": ["Passt Dienstag?"]}


def test_lifecycle_ignores_a_backward_jump() -> None:
    assert next_state(None, CallLifecycle.RINGING) is None
    assert next_state(None, CallLifecycle.DIALING) is CallLifecycle.DIALING
    assert next_state(CallLifecycle.DIALING, CallLifecycle.DIALING) is None
    assert next_state(CallLifecycle.DIALING, CallLifecycle.ANSWERED) is CallLifecycle.ANSWERED
    assert next_state(CallLifecycle.ANSWERED, CallLifecycle.RINGING) is None
    assert next_state(CallLifecycle.ENDED, CallLifecycle.FINALIZED) is CallLifecycle.FINALIZED


def test_listen_pref_defaults_off(tmp_path) -> None:
    path = tmp_path / "listen.json"
    assert load_listen_pref(path)["listen"] is False
    save_listen_pref(path, listen=False, volume=0.4, muted=True)
    saved = load_listen_pref(path)
    assert saved == {"listen": False, "volume": 0.4, "muted": True}
    save_listen_pref(path, listen=True, volume=2, muted=False)
    assert load_listen_pref(path)["listen"] is True
    assert load_listen_pref(path)["volume"] == 1.0


def test_audio_tap_drops_when_the_listener_is_full() -> None:
    from fernruf.monitor.hub import CallMonitor

    monitor = CallMonitor()
    monitor.begin("call-1", to_e164="+10000000000")
    slow = monitor.subscribe(listen=True, audio_queue_size=1)
    frame = InputAudioRawFrame(b"\x00\x00" * 160, 16000, 1)
    note_frame(monitor, "call-1", frame)
    assert slow.audio.qsize() == 1
    started = time.perf_counter()
    for _ in range(300):
        note_frame(monitor, "call-1", TTSAudioRawFrame(b"\x01\x00" * 80, 16000, 1))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05
    # The burst is re-clocked. Only the preroll is offered immediately, and
    # that hits the full queue. The rest stays buffered instead of being dumped.
    assert 0 < slow.dropped < 300
    kept = slow.audio.get_nowait()
    parsed = unpack_audio(kept)
    assert parsed["direction"] == "inbound"
    assert parsed["call_id"] == "call-1"
    assert monitor.wants_audio()


def test_app_exposes_the_monitor(tmp_path) -> None:
    settings, _token = service_settings(tmp_path)
    app = create_app(settings)
    assert app.state.monitor is not None


def test_monitor_refuses_a_public_bind() -> None:
    from fernruf.monitor.hub import CallMonitor

    async def run():
        with pytest.raises(BindError):
            await start_monitor_server(CallMonitor(), "x" * 48, host="0.0.0.0", port=0)

    asyncio.run(run())


def test_simulated_call_reaches_a_subscriber_without_stalling(tmp_path) -> None:
    settings, token = service_settings(tmp_path, FERNRUF_TRANSCRIPT_ALLOWED=True)
    manager = CallManager(
        settings,
        line_health=StaticLineHealth(True),
        now=lambda: datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN),
    )

    async def run():
        slow = manager.monitor.subscribe(listen=True, audio_queue_size=1)
        manager.monitor.offer_audio(
            "prefill",
            direction="inbound",
            pcm=b"\x00\x00",
            sample_rate=8000,
            channels=1,
        )
        assert slow.audio.full()
        burst = time.perf_counter()
        for _ in range(200):
            manager.monitor.offer_audio(
                "prefill",
                direction="outbound",
                pcm=b"\x00\x00" * 40,
                sample_rate=8000,
                channels=1,
            )
        assert time.perf_counter() - burst < 0.05
        assert slow.dropped >= 200

        server = await start_monitor_server(manager.monitor, token, port=0)
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{server.port}",
                compression=None,
                additional_headers={"Authorization": f"Bearer {token}"},
            ) as ws:
                ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert ack["type"] == "auth_ok"
                await ws.send(json.dumps({"v": 1, "type": "listen", "enabled": True}))
                listen_ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert listen_ack == {"v": 1, "type": "listen", "enabled": True}
                queued = await manager.place(
                    to="+493023125123",
                    task=TASK,
                    model="openai/gpt-test",
                )
                states: list[str] = []
                speakers: set[str] = set()
                directions: set[str] = set()
                finalized = None
                deadline = asyncio.get_running_loop().time() + 20
                while asyncio.get_running_loop().time() < deadline:
                    raw = await asyncio.wait_for(ws.recv(), 5)
                    if isinstance(raw, bytes):
                        directions.add(unpack_audio(raw)["direction"])
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "lifecycle":
                        states.append(msg["state"])
                        assert msg["to_masked"].endswith("5123")
                        assert msg["to_masked"] != "+493023125123"
                        if msg["state"] == "finalized":
                            finalized = msg
                            break
                    elif msg.get("type") == "transcript":
                        speakers.add(msg["speaker"])
                        assert msg["text"]
                        assert msg["final"] is True
                assert states[:5] == ["dialing", "ringing", "answered", "ended", "finalized"]
                assert speakers == {"agent", "callee"}
                assert directions == {"inbound", "outbound"}
                assert isinstance(finalized["summary"], dict)
                assert finalized["outcome"] == "completed"
                assert slow.dropped >= 200
                view = manager.get_call(queued["call_id"])
                assert view["status"] == "finalized"
                assert view["outcome"] == "completed"
            async with websockets.connect(f"ws://127.0.0.1:{server.port}", compression=None) as ws:
                await ws.send(json.dumps({"type": "auth", "token": "not-the-token"}))
                with pytest.raises(Exception):
                    await asyncio.wait_for(ws.recv(), 2)
        finally:
            await server.close()

    asyncio.run(run())
