"""Portrait ffmpeg render of a demo recording."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from fernruf.demo.captions import transcript_to_srt

DEMO_VIDEO_DISCLAIMER = "Both voices are AI. Demo call."
_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def notice_offset_ms(turns: list[dict]) -> int:
    """Milliseconds of recording before the first agent line (the AI notice)."""
    for turn in turns:
        if str(turn.get("speaker") or "") != "agent":
            continue
        if not str(turn.get("text") or "").strip():
            continue
        return max(0, int(turn.get("t_ms") or 0))
    return 0


def shift_turns(turns: list[dict], offset_ms: int) -> list[dict]:
    shifted: list[dict] = []
    for turn in turns:
        item = dict(turn)
        item["t_ms"] = max(0, int(turn.get("t_ms") or 0) - offset_ms)
        shifted.append(item)
    return shifted


def trim_wav(src: Path, dest: Path, offset_ms: int) -> Path:
    """Drop leading audio so the file starts at ``offset_ms``."""
    with wave.open(str(src), "rb") as handle:
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        frames = handle.getnframes()
        skip = 0 if rate <= 0 else int(rate * (offset_ms / 1000.0))
        skip = min(max(0, skip), max(0, frames - 1))
        handle.setpos(skip)
        audio = handle.readframes(frames - skip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(audio)
    return dest


def wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return handle.getnframes() / float(rate)
    except (wave.Error, EOFError, OSError):
        return None


def _escape_filter_path(path: Path) -> str:
    text = str(path)
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def _drawtext_font_option() -> str:
    if _FONT.is_file():
        return f"fontfile={_escape_filter_path(_FONT)}:"
    return ""


def demo_video_ffmpeg_args(
    *,
    wav: Path,
    srt: Path,
    mp4: Path,
    duration_s: float,
) -> list[str]:
    duration = max(0.5, float(duration_s))
    color = f"color=c=0x141414:s=1080x1920:r=30:d={duration:.3f}"
    srt_path = _escape_filter_path(srt)
    # Draw the disclaimer on the background plate, enabled at t=0, before the
    # waveform is overlaid. The text is then on frame 0, not only after the
    # first wave sample.
    draw = (
        f"drawtext={_drawtext_font_option()}text='{DEMO_VIDEO_DISCLAIMER}':"
        "fontsize=32:fontcolor=white:x=(w-text_w)/2:y=150:enable='gte(t,0)'"
    )
    style = (
        "FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00141414,BorderStyle=3,Alignment=2,MarginV=360"
    )
    graph = (
        f"[0:v]{draw}[bg];"
        "[1:a]aformat=channel_layouts=mono,"
        "showwaves=s=1080x480:mode=line:rate=30:colors=0xE6E6E6[wave];"
        "[bg][wave]overlay=(W-w)/2:(H-h)/2:eof_action=pass,"
        f"subtitles=filename='{srt_path}':force_style='{style}'[vout]"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        color,
        "-i",
        str(wav),
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(mp4),
    ]


def render_demo_video(*, wav: Path, mp4: Path, turns: list[dict]) -> Path:
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg is required for --video (libx264, aac, subtitles, drawtext)"
        )
    offset = notice_offset_ms(turns)
    with tempfile.TemporaryDirectory(prefix="fernruf-demo-") as tmp:
        work = Path(tmp)
        source = wav
        if offset > 0:
            trimmed = work / "call.wav"
            try:
                trim_wav(wav, trimmed, offset)
                source = trimmed
            except (wave.Error, EOFError, OSError):
                offset = 0
        duration = wav_duration_seconds(source)
        if duration is None or duration <= 0:
            duration = 30.0
        srt = work / "captions.srt"
        srt.write_text(
            transcript_to_srt(
                shift_turns(turns, offset),
                duration_ms=int(duration * 1000),
            ),
            encoding="utf-8",
        )
        args = demo_video_ffmpeg_args(
            wav=source,
            srt=srt,
            mp4=mp4,
            duration_s=duration,
        )
        completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to render {mp4}: {detail[:500]}")
    if not mp4.is_file():
        raise RuntimeError(f"ffmpeg did not write {mp4}")
    return mp4
