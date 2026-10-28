"""Portrait ffmpeg render of a demo recording."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

DEMO_VIDEO_DISCLAIMER = "Both voices are AI. Demo call."
_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


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
    draw = (
        f"drawtext={_drawtext_font_option()}text='{DEMO_VIDEO_DISCLAIMER}':"
        "fontsize=32:fontcolor=white:x=(w-text_w)/2:y=150"
    )
    style = (
        "FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00141414,BorderStyle=3,Alignment=2,MarginV=360"
    )
    graph = (
        "[1:a]aformat=channel_layouts=mono,"
        "showwaves=s=1080x480:mode=line:rate=30:colors=0xE6E6E6[wave];"
        f"[0:v][wave]overlay=(W-w)/2:(H-h)/2,{draw},"
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


def render_demo_video(*, wav: Path, srt: Path, mp4: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg is required for --video (libx264, aac, subtitles, drawtext)"
        )
    duration = wav_duration_seconds(wav)
    if duration is None or duration <= 0:
        duration = 30.0
    args = demo_video_ffmpeg_args(wav=wav, srt=srt, mp4=mp4, duration_s=duration)
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to render {mp4}: {detail[:500]}")
    if not mp4.is_file():
        raise RuntimeError(f"ffmpeg did not write {mp4}")
    return mp4
