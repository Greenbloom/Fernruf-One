"""Local demo call: dialplan, trunk guard, and captions. No live Asterisk."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from click.testing import CliRunner

from fernruf.cli import main
from fernruf.demo.captions import FERNRUF_CAPTION, PRAXIS_CAPTION, transcript_to_srt
from fernruf.demo.dialplan import demo_extensions_conf, monitor_id_from_endpoint
from fernruf.demo.guard import DemoTrunkError, assert_local_demo_endpoint
from fernruf.demo.persona import (
    DEFAULT_DEMO_PERSONA,
    DEFAULT_DEMO_TASK,
    DEFAULT_MAX_SECONDS,
    resolve_callee_voice,
)
from fernruf.demo.plan import plan_demo_call
from fernruf.demo.summary import build_demo_summary
from fernruf.demo.video import (
    DEMO_VIDEO_DISCLAIMER,
    demo_video_ffmpeg_args,
    notice_offset_ms,
    shift_turns,
    trim_wav,
    wav_duration_seconds,
)

ROOT = Path(__file__).resolve().parents[1]


def test_demo_dialplan_records_locally_and_matches_the_shipped_file() -> None:
    generated = demo_extensions_conf()
    shipped = (ROOT / "docker/asterisk/templates/extensions.demo.conf").read_text()
    assert generated == shipped
    assert "[fernruf-demo]" in generated
    assert "MixMonitor(/var/spool/asterisk/monitor/${EXTEN:4}.wav)" in generated
    assert "Dial(WebSocket/fernruf-callee/c(slin16)f(json)d(both))" in generated
    assert "sip-trunk" not in generated
    assert "PJSIP" not in generated


def test_rendered_demo_context_has_no_trunk_route() -> None:
    base = (ROOT / "docker/asterisk/templates/extensions.conf.template").read_text()
    rendered = base + demo_extensions_conf()
    demo = rendered.split("[fernruf-demo]", 1)[1]
    assert "sip-trunk" not in demo
    assert "PJSIP" not in demo
    websocket = (ROOT / "docker/asterisk/templates/websocket_client.conf.template").read_text()
    assert "[fernruf-callee]" in websocket
    assert "${FERNRUF_DEMO_CALLEE_WS_URI}" in websocket
    entry = (ROOT / "docker/asterisk/entrypoint.sh").read_text()
    assert "extensions.demo.conf" in entry
    assert "FERNRUF_DEMO_CALLEE_WS_URI" in entry
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "./data/asterisk-monitor:/var/spool/asterisk/monitor" in compose


def test_trunk_endpoint_is_refused() -> None:
    for endpoint in (
        "PJSIP/example@sip-trunk",
        "Local/600-abc12345@fernruf-demo/n@sip-trunk",
        "PJSIP/Local/600-abc12345@fernruf-demo/n",
        "Local/600-abc12345@fernruf-demo",
        "",
    ):
        try:
            assert_local_demo_endpoint(endpoint)
        except DemoTrunkError as exc:
            assert "refuses" in str(exc)
        else:
            raise AssertionError(endpoint)


def test_planned_endpoint_stays_on_the_local_extension() -> None:
    plan = plan_demo_call(Path("data/demo/launch"), call_id="abc12345")
    assert plan.endpoint == "Local/600-abc12345@fernruf-demo/n"
    assert assert_local_demo_endpoint(plan.endpoint) == plan.endpoint
    assert "sip-trunk" not in plan.endpoint
    assert "PJSIP" not in plan.endpoint
    assert monitor_id_from_endpoint(plan.endpoint) == "abc12345"
    assert plan.monitor_container_path == "/var/spool/asterisk/monitor/abc12345.wav"
    assert plan.monitor_host_path == Path("data/asterisk-monitor/abc12345.wav")


def test_srt_labels_and_timestamps_follow_the_recording() -> None:
    srt = transcript_to_srt(
        [
            {"t_ms": 0, "speaker": "agent", "text": "Hier ist ein KI-Assistent."},
            {"t_ms": 3200, "speaker": "callee", "text": "Praxis Beispiel, guten Tag."},
        ],
        duration_ms=8000,
    )
    assert srt.startswith(
        "1\n00:00:00,000 --> 00:00:03,120\n"
        f"{FERNRUF_CAPTION}: Hier ist ein KI-Assistent.\n"
    )
    assert f"{PRAXIS_CAPTION}: Praxis Beispiel, guten Tag." in srt
    assert "00:00:03,200 -->" in srt


def test_summary_uses_the_final_agent_line() -> None:
    summary = build_demo_summary(
        call_id="abc12345",
        task="Termin am Dienstag",
        persona=DEFAULT_DEMO_PERSONA,
        transcript=[
            {"t_ms": 0, "speaker": "agent", "text": "Hinweis"},
            {"t_ms": 1000, "speaker": "callee", "text": "Dienstag passt."},
            {"t_ms": 2000, "speaker": "agent", "text": "Auf Wiederhören."},
        ],
        duration_s=4.2,
        answered=True,
    )
    assert summary["task_status"] == "completed"
    assert summary["one_line"] == "Auf Wiederhören."
    assert summary["local_only"] is True


def test_default_persona_and_voice_fallback() -> None:
    assert "Praxis Beispiel" in DEFAULT_DEMO_PERSONA
    assert "Dienstag" in DEFAULT_DEMO_PERSONA
    assert "end_call" in DEFAULT_DEMO_TASK
    assert len(DEFAULT_DEMO_TASK) < 220
    assert len(DEFAULT_DEMO_PERSONA) < 180
    assert DEFAULT_MAX_SECONDS == 30
    assert resolve_callee_voice("", "caller-voice") == "caller-voice"
    assert resolve_callee_voice("  ", "caller-voice") == "caller-voice"
    assert resolve_callee_voice("callee-voice", "caller-voice") == "callee-voice"


def test_video_command_is_portrait_with_disclaimer() -> None:
    args = demo_video_ffmpeg_args(
        wav=Path("data/demo/launch/call.wav"),
        srt=Path("data/demo/launch/captions.srt"),
        mp4=Path("data/demo/launch/demo.mp4"),
        duration_s=12.5,
    )
    joined = " ".join(args)
    graph = args[args.index("-filter_complex") + 1]
    assert "1080x1920" in joined
    assert "showwaves" in joined
    assert DEMO_VIDEO_DISCLAIMER in joined
    assert graph.startswith("[0:v]drawtext=")
    assert "enable='gte(t,0)'" in graph
    assert graph.index("drawtext=") < graph.index("overlay=")
    assert "captions.srt" in joined
    assert args[-1].endswith("demo.mp4")
    assert "sip-trunk" not in joined


def test_video_clip_starts_at_the_ai_notice(tmp_path: Path) -> None:
    wav = tmp_path / "call.wav"
    rate = 8000
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (rate * 2))
    turns = [
        {"t_ms": 500, "speaker": "agent", "text": "Hier ist ein KI-Assistent."},
        {"t_ms": 1200, "speaker": "callee", "text": "Dienstag passt."},
    ]
    assert notice_offset_ms(turns) == 500
    srt = transcript_to_srt(shift_turns(turns, notice_offset_ms(turns)))
    assert srt.startswith("1\n00:00:00,000 -->")
    assert f"{FERNRUF_CAPTION}: Hier ist ein KI-Assistent." in srt
    trimmed = tmp_path / "clip.wav"
    trim_wav(wav, trimmed, 500)
    assert wav_duration_seconds(trimmed) == 1.5


def test_demo_call_help_lists_local_options() -> None:
    result = CliRunner().invoke(main, ["demo-call", "--help"])
    assert result.exit_code == 0
    for flag in ("--task", "--persona", "--out", "--video", "--max-seconds"):
        assert flag in result.output
    assert "30" in result.output


def test_time_limit_ends_via_end_call() -> None:
    from fernruf.demo.limit import end_demo_politely
    from fernruf.voice.call_session import M2CallSession
    from fernruf.voice.hooks import M2CallHooks

    async def _noop() -> None:
        return None

    async def _run() -> None:
        session = M2CallSession()
        armed: list[str] = []

        async def _schedule() -> None:
            armed.append("hangup")

        hooks = M2CallHooks(
            send_dtmf=_noop,
            hangup=_noop,
            schedule_hangup=_schedule,
            session=session,
        )
        assert await end_demo_politely(session, hooks, "Auf Wiederhören.")
        assert session.phase.is_ending
        assert session.phase.goodbye_tts == "Auf Wiederhören."
        assert session.phase.hangup_reason == "demo time limit"
        assert armed == ["hangup"]

    asyncio.run(_run())
