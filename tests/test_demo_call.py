"""Local demo call: dialplan, trunk guard, and captions. No live Asterisk."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.demo.captions import FERNRUF_CAPTION, PRAXIS_CAPTION, transcript_to_srt
from fernruf.demo.dialplan import demo_extensions_conf, monitor_id_from_endpoint
from fernruf.demo.guard import DemoTrunkError, assert_local_demo_endpoint
from fernruf.demo.callee import settings_for_callee
from fernruf.demo.persona import (
    DEFAULT_DEMO_PERSONA,
    DEFAULT_DEMO_TASK,
    DEFAULT_MAX_SECONDS,
    DemoVoiceError,
    apply_demo_caller_voice,
    require_demo_voices,
)
from fernruf.language import prepare_call_settings
from fernruf.settings import Settings
from fernruf.demo.plan import plan_demo_call
from fernruf.demo.summary import build_demo_summary
from fernruf.demo.phone_screen import (
    AVATAR,
    BG,
    CONTACT_NAME,
    DEMO_CALL_LABEL,
    FERNRUF_BUBBLE,
    HEIGHT,
    LABEL_BOTTOM,
    LABEL_TOP,
    PRAXIS_BUBBLE,
    SPEAK,
    WIDTH,
    cues_from_srt,
    format_timer,
    render_phone_frame,
)
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
    assert "Set(AUDIOHOOK_INHERIT(MixMonitor)=yes)" in generated
    assert "MixMonitor(/var/spool/asterisk/monitor/${EXTEN:4}.wav)" in generated
    assert "exten => h,1,StopMixMonitor()" in generated
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
    assert "ln -s /var/spool/asterisk/monitor /var/spool/asterisk/recording" in entry
    testing = (ROOT / "docs/TESTING.md").read_text()
    assert "docker compose up -d --build --force-recreate asterisk" in testing
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
    assert plan.bridge_recording_host_path == Path("data/asterisk-monitor/demo-abc12345.wav")


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
    assert "00:00:03,200 --> 00:00:08,000" in srt


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


def _demo_settings(**overrides: str) -> Settings:
    fields = {
        "fish_voice_id": "operator-voice",
        "demo_caller_voice_id": "library-caller",
        "demo_callee_voice_id": "library-callee",
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def test_default_persona_is_short() -> None:
    assert "Praxis Beispiel" in DEFAULT_DEMO_PERSONA
    assert "Dienstag" in DEFAULT_DEMO_PERSONA
    assert "end_call" in DEFAULT_DEMO_TASK
    assert len(DEFAULT_DEMO_TASK) < 220
    assert len(DEFAULT_DEMO_PERSONA) < 180
    assert DEFAULT_MAX_SECONDS == 30


def test_demo_voices_are_required_and_not_the_operator_voice() -> None:
    caller, callee = require_demo_voices(_demo_settings())
    assert caller == "library-caller"
    assert callee == "library-callee"

    missing_caller = _demo_settings(demo_caller_voice_id="  ")
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLER_VOICE_ID"):
        require_demo_voices(missing_caller)

    missing_callee = _demo_settings(demo_callee_voice_id="")
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLEE_VOICE_ID"):
        require_demo_voices(missing_callee)

    both_missing = _demo_settings(demo_caller_voice_id="", demo_callee_voice_id="")
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLER_VOICE_ID and FERNRUF_DEMO_CALLEE_VOICE_ID"):
        require_demo_voices(both_missing)

    same_caller = _demo_settings(demo_caller_voice_id="operator-voice")
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLER_VOICE_ID"):
        require_demo_voices(same_caller)

    same_callee = _demo_settings(demo_callee_voice_id=" operator-voice ")
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLEE_VOICE_ID"):
        require_demo_voices(same_callee)

    callee_settings = settings_for_callee(_demo_settings())
    assert callee_settings.fish_voice_id == "library-callee"
    with pytest.raises(DemoVoiceError, match="FERNRUF_DEMO_CALLEE_VOICE_ID"):
        settings_for_callee(_demo_settings(demo_callee_voice_id=""))


def test_demo_caller_voice_is_not_replaced_by_the_operator_voice(monkeypatch) -> None:
    monkeypatch.setenv("FERNRUF_FISH_VOICE_ID_DE", "operator-de")
    monkeypatch.setenv("FISH_VOICE_ID", "operator-voice")
    settings = _demo_settings()

    async def _run() -> None:
        prepared = await prepare_call_settings(settings, "Dienstag", language="de")
        assert prepared.fish_voice_id == "operator-de"
        pinned = apply_demo_caller_voice(prepared)
        assert pinned.fish_voice_id == "library-caller"
        again = await prepare_call_settings(pinned, "Dienstag", language="de")
        assert again.fish_voice_id == "library-caller"
        fresh = apply_demo_caller_voice(_demo_settings())
        from_pin = await prepare_call_settings(fresh, "Dienstag", language="de")
        assert from_pin.fish_voice_id == "library-caller"
        assert settings_for_callee(from_pin).fish_voice_id == "library-callee"
        replaced = await prepare_call_settings(
            _demo_settings(demo_caller_voice_id="operator-voice"),
            "Dienstag",
            language="de",
        )
        assert replaced.fish_voice_id == "operator-de"
        with pytest.raises(DemoVoiceError, match="matches FISH_VOICE_ID"):
            apply_demo_caller_voice(replaced)

    asyncio.run(_run())


def test_demo_call_refuses_without_library_voices(monkeypatch) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.delenv("FERNRUF_DEMO_CALLER_VOICE_ID", raising=False)
    monkeypatch.delenv("FERNRUF_DEMO_CALLEE_VOICE_ID", raising=False)
    monkeypatch.setenv("FISH_VOICE_ID", "operator-voice")
    result = CliRunner().invoke(main, ["demo-call", "--out", "data/demo/voice-check"])
    assert result.exit_code == 2
    assert "FERNRUF_DEMO_CALLER_VOICE_ID" in result.output
    assert "does not fall back to FISH_VOICE_ID" in result.output


def test_video_command_assembles_phone_frames() -> None:
    args = demo_video_ffmpeg_args(
        frames_pattern="frames/%05d.png",
        wav=Path("data/demo/launch/call.wav"),
        mp4=Path("data/demo/launch/demo.mp4"),
        fps=12,
    )
    joined = " ".join(args)
    assert "showwaves" not in joined
    assert "drawtext" not in joined
    assert "frames/%05d.png" in joined
    assert "libx264" in args
    assert args[args.index("-framerate") + 1] == "12"
    assert args[-1].endswith("demo.mp4")
    assert "sip-trunk" not in joined
    assert DEMO_VIDEO_DISCLAIMER == DEMO_CALL_LABEL


def test_phone_screen_shows_the_label_from_the_first_frame() -> None:
    quiet = render_phone_frame(0, [])
    assert quiet.size == (WIDTH, HEIGHT)
    assert quiet.getpixel((WIDTH // 2, 168)) != BG
    assert _band_is_lit(quiet, LABEL_TOP, LABEL_BOTTOM)
    assert format_timer(0) == "00:00"
    assert format_timer(65) == "01:05"
    assert CONTACT_NAME == "Praxis Beispiel"
    assert "·" in DEMO_CALL_LABEL


def test_chat_bubbles_follow_the_captions() -> None:
    srt = transcript_to_srt(
        [
            {"t_ms": 1000, "speaker": "agent", "text": "Passt Dienstag?"},
            {"t_ms": 2500, "speaker": "callee", "text": "Dienstag um zehn Uhr passt."},
        ]
    )
    cues = cues_from_srt(srt)
    assert [cue.side for cue in cues] == ["fernruf", "praxis"]
    before = render_phone_frame(0.2, cues)
    during = render_phone_frame(1.2, cues, frame_index=2)
    both = render_phone_frame(3.0, cues, frame_index=4)
    assert not _has_color(before, FERNRUF_BUBBLE)
    assert not _has_color(before, PRAXIS_BUBBLE)
    assert not _has_color(before, SPEAK)
    assert _has_color(during, FERNRUF_BUBBLE)
    assert not _has_color(during, PRAXIS_BUBBLE)
    assert _has_color(during, SPEAK)
    assert _color_on_side(during, FERNRUF_BUBBLE, "left")
    assert _has_color(both, FERNRUF_BUBBLE)
    assert _has_color(both, PRAXIS_BUBBLE)
    assert _color_on_side(both, PRAXIS_BUBBLE, "right")
    assert _band_is_lit(before, LABEL_TOP, LABEL_BOTTOM)
    assert quiet_avatar_is_generic(during)


def quiet_avatar_is_generic(image) -> bool:
    return image.getpixel((WIDTH // 2, 168)) in {AVATAR, (198, 206, 196)}


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


def test_captions_cover_the_spoken_line_not_a_flash() -> None:
    turns = [
        {"t_ms": 98, "speaker": "agent", "text": "Hier ist ein KI-Assistent. Passt Dienstag?"},
        {"t_ms": 12934, "speaker": "callee", "text": "Dienstag um 10 Uhr passt."},
        {"t_ms": 17369, "speaker": "agent", "text": "Auf Wiederhören."},
    ]
    full = transcript_to_srt(turns, duration_ms=25000)
    assert "00:00:00,098 --> 00:00:12,854" in full
    assert "00:00:12,934 --> 00:00:17,289" in full
    assert "00:00:17,369 --> 00:00:25,000" in full
    short = transcript_to_srt(turns, duration_ms=11200)
    assert "00:00:12,934 --> 00:00:13,334" not in short
    assert "00:00:17,369 --> 00:00:17,769" not in short
    assert "00:00:12,934 --> 00:00:17,289" in short


def test_opening_asks_the_tuesday_question() -> None:
    from fernruf.demo.persona import (
        DEFAULT_DEMO_REQUEST,
        callee_system_prompt,
        spoken_demo_request,
    )
    from fernruf.voice.must_say import goal_needs_answer

    assert spoken_demo_request(DEFAULT_DEMO_TASK) == DEFAULT_DEMO_REQUEST
    assert DEFAULT_DEMO_REQUEST == "Passt Dienstag für einen Termin?"
    assert goal_needs_answer(DEFAULT_DEMO_TASK) is True
    prompt = callee_system_prompt(DEFAULT_DEMO_PERSONA)
    assert "KI-Assistent" in prompt
    assert "konkrete Frage" in prompt
    custom = spoken_demo_request("Bitte fragen, ob der Termin passt. Ist Mittwoch frei?")
    assert custom == "Ist Mittwoch frei?"


def _silent_wav(path: Path, seconds: float, *, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(round(seconds * rate)))


def _recording_plan(tmp: Path) -> object:
    from fernruf.demo.plan import DemoCallPlan

    monitor = tmp / "monitor"
    return DemoCallPlan(
        call_id="abc12345",
        endpoint="Local/600-abc12345@fernruf-demo/n",
        out=tmp / "out",
        monitor_container_path="/var/spool/asterisk/monitor/abc12345.wav",
        monitor_host_path=monitor / "abc12345.wav",
        bridge_recording_host_path=monitor / "demo-abc12345.wav",
    )


def test_publish_recording_waits_for_the_full_call(tmp_path: Path) -> None:
    from fernruf.demo.recording import publish_recording, ready_recording, recording_sources

    plan = _recording_plan(tmp_path)
    _silent_wav(plan.monitor_host_path, 11.2)
    _silent_wav(plan.bridge_recording_host_path, 18.0)
    sources = recording_sources(plan)
    growing = {plan.monitor_host_path: 2, plan.bridge_recording_host_path: 0}
    assert ready_recording(sources, growing, min_seconds=17.0) is None
    stable = {plan.monitor_host_path: 2, plan.bridge_recording_host_path: 2}
    assert ready_recording(sources, stable, min_seconds=17.0) == plan.bridge_recording_host_path
    copied = asyncio.run(publish_recording(plan, min_seconds=17.0, timeout_s=3))
    assert wav_duration_seconds(copied) == pytest.approx(18.0, abs=0.05)


def test_short_mix_is_not_published(tmp_path: Path) -> None:
    from fernruf.demo.recording import publish_recording

    plan = _recording_plan(tmp_path)
    _silent_wav(plan.monitor_host_path, 11.2)
    with pytest.raises(FileNotFoundError, match="Need at least 17.0s"):
        asyncio.run(publish_recording(plan, min_seconds=17.0, timeout_s=0.6))
    assert not (plan.out / "call.wav").exists()


def test_unclosed_wav_header_is_repaired_from_the_payload(tmp_path: Path) -> None:
    from fernruf.demo.recording import publish_recording, wav_duration_seconds as recorded

    plan = _recording_plan(tmp_path)
    _silent_wav(plan.bridge_recording_host_path, 18.0)
    blob = bytearray(plan.bridge_recording_host_path.read_bytes())
    blob[4:8] = (0).to_bytes(4, "little")
    blob[40:44] = (0).to_bytes(4, "little")
    plan.bridge_recording_host_path.write_bytes(blob)
    assert recorded(plan.bridge_recording_host_path) == pytest.approx(18.0, abs=0.05)
    copied = asyncio.run(publish_recording(plan, min_seconds=17.0, timeout_s=3))
    assert wav_duration_seconds(copied) == pytest.approx(18.0, abs=0.05)


def test_demo_call_returns_without_waiting_for_executor_threads(monkeypatch) -> None:
    import threading
    import time

    from fernruf.demo.shutdown import exit_demo_process, run_until_done

    started = threading.Event()
    release = threading.Event()

    def _block() -> None:
        started.set()
        release.wait(30)

    async def _main() -> str:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _block)
        while not started.is_set():
            await asyncio.sleep(0.01)
        return "done"

    t0 = time.monotonic()
    try:
        assert run_until_done(_main()) == "done"
        assert time.monotonic() - t0 < 2.0
    finally:
        release.set()

    monkeypatch.setenv("FERNRUF_DEMO_KEEP_PROCESS", "1")
    with pytest.raises(SystemExit) as exc:
        exit_demo_process(0)
    assert exc.value.code == 0


def _band_is_lit(image, top: int, bottom: int) -> bool:
    crop = image.crop((0, top, image.width, bottom))
    extrema = crop.getextrema()
    return max(channel[1] for channel in extrema) > 150


def _has_color(image, color: tuple[int, int, int]) -> bool:
    return color in _palette(image)


def _color_on_side(image, color: tuple[int, int, int], side: str) -> bool:
    half = image.width // 2
    box = (0, 0, half, image.height) if side == "left" else (half, 0, image.width, image.height)
    return color in _palette(image.crop(box))


def _palette(image) -> set[tuple[int, int, int]]:
    found = image.getcolors(maxcolors=image.width * image.height)
    if not found:
        return set()
    return {entry[1] for entry in found}
