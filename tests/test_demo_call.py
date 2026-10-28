"""Local demo call: dialplan, trunk guard, and captions. No live Asterisk."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from fernruf.cli import main
from fernruf.demo.captions import FERNRUF_CAPTION, PRAXIS_CAPTION, transcript_to_srt
from fernruf.demo.dialplan import demo_extensions_conf, monitor_id_from_endpoint
from fernruf.demo.guard import DemoTrunkError, assert_local_demo_endpoint
from fernruf.demo.persona import DEFAULT_DEMO_PERSONA, resolve_callee_voice
from fernruf.demo.plan import plan_demo_call
from fernruf.demo.summary import build_demo_summary
from fernruf.demo.video import DEMO_VIDEO_DISCLAIMER, demo_video_ffmpeg_args

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
    assert "1080x1920" in joined
    assert "showwaves" in joined
    assert DEMO_VIDEO_DISCLAIMER in joined
    assert "captions.srt" in joined
    assert args[-1].endswith("demo.mp4")
    assert "sip-trunk" not in joined


def test_demo_call_help_lists_local_options() -> None:
    result = CliRunner().invoke(main, ["demo-call", "--help"])
    assert result.exit_code == 0
    for flag in ("--task", "--persona", "--out", "--video"):
        assert flag in result.output
