from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

import pytest
from tests.m3_support import service_settings

from fernruf.call_settings.store import get_call_settings, transcript_allowed, update_call_settings
from fernruf.cli import main
from fernruf.monitor.server import start_monitor_server
from fernruf.quiet_hours import BERLIN
from fernruf.service.guide import render_calling_guide
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallManager, limits_for
from fernruf.service.records import get_stored_call, save_webhook
from fernruf.service.runner import CallJob, CallRunResult
from fernruf.service.settings_update import SettingsUpdateError, apply_mcp_settings
from fernruf.voice.call_log import attach_call_log_handler, detach_call_log_handler

SENTINEL = "The blue folder is on the left shelf."
NOON = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
TASK = {"goal": "Ask whether the blue folder is still on the shelf."}


def _manager(tmp_path, *, runner, poster, summarize=None, **overrides):
    settings, token = service_settings(tmp_path, **overrides)
    manager = CallManager(
        settings,
        line_health=StaticLineHealth(True),
        runner=runner,
        now=lambda: NOON,
        poster=poster,
        summarize=summarize,
        keepalive_s=0.05,
    )
    return manager, settings, token


def _runner(calls: list[str]):
    async def runner(job: CallJob) -> CallRunResult:
        calls.append(job.call_id)
        monitor = job.monitor
        if monitor is not None:
            monitor.note_transcript(
                job.call_id,
                speaker="callee",
                text=SENTINEL,
                t_ms=20,
                final=True,
            )
        return CallRunResult(
            outcome="completed",
            answered=True,
            duration_s=3.0,
            transcript=[
                {"t_ms": 0, "speaker": "agent", "text": "Hello."},
                {"t_ms": 20, "speaker": "callee", "text": SENTINEL},
            ],
        )

    return runner


def _summary(seen: dict):
    async def summarize(**kwargs):
        seen["transcript"] = list(kwargs["transcript"])
        return {
            "outcome": "folder located",
            "key_facts": ["The folder is still there."],
            "follow_ups": ["Send the shelf photo."],
            "consent": "not_asked",
            "one_line": "The folder is still there.",
            "task_status": "completed",
        }

    return summarize


async def _finish(manager, settings, token, posted: list[dict]):
    save_webhook(
        settings,
        webhook_id="wh-transcript",
        url="https://example.com/hooks/calls",
        events=["call.finalized"],
        created_at=NOON.isoformat(),
    )
    server = await start_monitor_server(manager.monitor, token, port=0)
    import websockets

    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{server.port}",
            compression=None,
            additional_headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert ack["type"] == "auth_ok"
            queued = await manager.place(to="+493023125123", task=TASK)
            messages: list[dict] = []
            deadline = asyncio.get_running_loop().time() + 10
            while asyncio.get_running_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                messages.append(msg)
                if msg.get("type") == "lifecycle" and msg.get("state") == "finalized":
                    break
            else:
                raise AssertionError("call did not finish")
    finally:
        await server.close()
    view = manager.get_call(queued["call_id"])
    stored = get_stored_call(settings, queued["call_id"]) or {}
    raw_db = tmp_db(settings).read_bytes().decode("utf-8", errors="ignore")
    return view, stored, messages, posted, raw_db


def tmp_db(settings):
    from pathlib import Path

    return Path(settings.call_settings_db)


def test_transcript_off_keeps_the_summary_and_drops_the_words(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="fernruf.transcript")
    posted: list[dict] = []

    async def poster(_url: str, body: dict) -> bool:
        posted.append(body)
        return True

    seen: dict = {}
    manager, settings, token = _manager(
        tmp_path,
        runner=_runner([]),
        poster=poster,
        summarize=_summary(seen),
    )
    view, stored, messages, bodies, raw_db = asyncio.run(_finish(manager, settings, token, posted))

    assert seen["transcript"][1]["text"] == SENTINEL
    assert view["summary"]["key_facts"] == ["The folder is still there."]
    assert view["summary"]["follow_ups"] == ["Send the shelf photo."]
    assert view["summary"]["consent"] == "not_asked"
    assert view["transcript"] is None
    assert view["transcript_reason"] == "transcript_disabled"
    assert view["last_lines"] == []
    assert stored.get("transcript") == []
    assert stored.get("transcript_allowed") is False
    assert SENTINEL not in raw_db
    assert "Hello." not in raw_db
    assert bodies
    blob = json.dumps(bodies[0])
    assert SENTINEL not in blob
    assert bodies[0]["data"]["transcript"] is None
    assert bodies[0]["data"]["transcript_reason"] == "transcript_disabled"
    assert bodies[0]["data"]["summary"]["outcome"] == "folder located"
    kinds = [msg.get("type") for msg in messages]
    assert "transcript" not in kinds
    assert "lifecycle" in kinds
    assert any(msg.get("state") == "dialing" for msg in messages)
    assert any(msg.get("transcript_allowed") is False for msg in messages)
    assert "transcript_allowed=false" in caplog.text
    guide = render_calling_guide(
        settings,
        limits_for(settings),
        line_ready=True,
        spent_eur=0,
        now_label="2026-06-15 12:00",
    )
    assert "Transcript storage is off" in guide
    assert "fernruf settings transcript on" in guide


def test_transcript_on_keeps_the_current_record(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="fernruf.transcript")
    posted: list[dict] = []

    async def poster(_url: str, body: dict) -> bool:
        posted.append(body)
        return True

    seen: dict = {}
    manager, settings, token = _manager(
        tmp_path,
        runner=_runner([]),
        poster=poster,
        summarize=_summary(seen),
        FERNRUF_TRANSCRIPT_ALLOWED=True,
    )
    view, stored, messages, bodies, raw_db = asyncio.run(_finish(manager, settings, token, posted))

    assert view["summary"]["outcome"] == "folder located"
    assert view["transcript"]["complete"] is True
    assert view["transcript"]["lines"][1]["text"] == SENTINEL
    assert "transcript_reason" not in view
    assert stored.get("transcript_allowed") is True
    assert SENTINEL in raw_db
    assert SENTINEL in json.dumps(bodies[0])
    assert bodies[0]["data"]["transcript"]["lines"][1]["text"] == SENTINEL
    spoken = [msg for msg in messages if msg.get("type") == "transcript"]
    assert spoken
    assert spoken[0]["text"] == SENTINEL
    assert spoken[0]["speaker"] == "callee"
    assert any(msg.get("transcript_allowed") is True for msg in messages)
    assert "transcript_allowed=true" in caplog.text
    guide = render_calling_guide(
        settings,
        limits_for(settings),
        line_ready=True,
        spent_eur=0,
        now_label="2026-06-15 12:00",
    )
    assert "Transcript storage is on" in guide


def test_env_seeds_the_row_and_the_cli_can_change_it(tmp_path) -> None:
    settings, _token = service_settings(tmp_path, FERNRUF_TRANSCRIPT_ALLOWED=True)
    assert get_call_settings(settings).transcript_allowed is True
    assert transcript_allowed(settings) is True

    conn = sqlite3.connect(settings.call_settings_db)
    raw = json.loads(conn.execute("SELECT json FROM call_settings WHERE id = 1").fetchone()[0])
    raw.pop("transcript_allowed")
    conn.execute(
        "UPDATE call_settings SET json = ? WHERE id = 1",
        (json.dumps(raw),),
    )
    conn.commit()
    conn.close()
    assert get_call_settings(settings).transcript_allowed is True

    from click.testing import CliRunner

    env = {
        "FERNRUF_ENV_FILE": "none",
        "FERNRUF_SETTINGS_DB": str(tmp_path / "fernruf.sqlite"),
        "FERNRUF_PRINCIPAL_NAME": "Alex Example",
        "FERNRUF_LLM_MODEL": "openrouter/test-model",
        "FERNRUF_TRANSCRIPT_ALLOWED": "false",
    }
    turned_off = CliRunner().invoke(main, ["settings", "transcript", "off"], env=env)
    assert turned_off.exit_code == 0, turned_off.output
    assert transcript_allowed(settings) is False
    turned_on = CliRunner().invoke(main, ["settings", "transcript", "on"], env=env)
    assert turned_on.exit_code == 0, turned_on.output
    assert "transcript_allowed=true" in turned_on.output
    assert transcript_allowed(settings) is True

    with pytest.raises(SettingsUpdateError) as exc:
        apply_mcp_settings(settings, {"transcript_allowed": False})
    assert exc.value.code == "setting_requires_cli"


def test_call_log_omits_spoken_text_when_transcripts_are_off(tmp_path) -> None:
    _assert_call_log(tmp_path, allowed=False)
    _assert_call_log(tmp_path / "on", allowed=True)


def _assert_call_log(tmp_path, *, allowed: bool) -> None:
    settings, _token = service_settings(
        tmp_path,
        FERNRUF_TRANSCRIPT_ALLOWED=allowed,
    )
    logging.getLogger("fernruf").setLevel(logging.INFO)
    path = attach_call_log_handler("call-1", base_dir=tmp_path / "calls")
    try:
        from fernruf.voice.processors.metrics import ConversationMetricsProcessor

        asyncio.run(_speak(settings))
        metrics = ConversationMetricsProcessor(
            call_id="call-1",
            provider="openrouter",
            model="test",
            latency_log=None,
            settings=settings,
        )
        frame = _transcription(SENTINEL)
        asyncio.run(metrics.process_frame(frame, _downstream()))
    finally:
        detach_call_log_handler("call-1")
    body = path.read_text(encoding="utf-8")
    if allowed:
        assert body.count(SENTINEL) >= 2
    else:
        assert SENTINEL not in body
        assert "chars" in body


def _transcription(text: str):
    from pipecat.frames.frames import TranscriptionFrame

    return TranscriptionFrame(text=text, user_id="u", timestamp="t")


def _downstream():
    from pipecat.processors.frame_processor import FrameDirection

    return FrameDirection.DOWNSTREAM


async def _speak(settings) -> None:
    from fernruf.voice.services.faster_whisper_stt import FasterWhisperSTTService
    from fernruf.voice.services.fish_rest_tts import FishRestTTSService

    stt = FasterWhisperSTTService(call_settings=settings, mock_text=SENTINEL)
    async for _frame in stt.run_stt(b"\x00\x00" * 2000):
        pass
    tts = FishRestTTSService(call_settings=settings, use_fixture=True)
    async for _frame in tts.run_tts(SENTINEL, "ctx"):
        pass


def test_fresh_database_defaults_transcripts_off(tmp_path) -> None:
    settings, _token = service_settings(tmp_path)
    assert settings.transcript_allowed is False
    assert transcript_allowed(settings) is False
    saved = get_call_settings(settings)
    assert saved.transcript_allowed is False
    update_call_settings({"persona_name": "Desk"}, settings=settings)
    assert get_call_settings(settings).transcript_allowed is False


def test_monitor_window_names_the_mode(tmp_path) -> None:
    pytest.importorskip("PySide6")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    script = r"""
import sys
from pathlib import Path

from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication

from fernruf.desktop.app import CallModel, _load_window

app = QApplication(["fernruf-transcript-mode"])
model = CallModel(Path("/tmp/fernruf-transcript-mode.json"))
window = _load_window(model)
assert window is not None
window.show()
app.processEvents()

def item(name):
    found = window.findChild(QQuickItem, name)
    assert found is not None, name
    return found

model.apply_lifecycle({
    "state": "answered",
    "to_masked": "+49 30 •••• 5123",
    "transcript_allowed": False,
    "summary": None,
})
app.processEvents()
assert model.state == "answered"
assert model.number.startswith("+49")
assert item("transcriptPanel").isVisible()
assert item("transcriptOffNote").isVisible()
assert item("transcriptDisabled").isVisible()
assert item("transcriptNotice").isVisible() is False
assert item("transcriptOffNote").property("text") == "Transcript is off."

model.apply_lifecycle({
    "state": "answered",
    "to_masked": "+49 30 •••• 5123",
    "transcript_allowed": True,
    "summary": None,
})
model.apply_transcript({
    "speaker": "callee",
    "text": "The blue folder is on the left shelf.",
    "final": True,
})
app.processEvents()
assert item("transcriptNotice").isVisible()
assert item("transcriptDisabled").isVisible() is False
assert item("transcriptOffNote").isVisible() is False
assert "transcribed" in item("transcriptNotice").property("text")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
