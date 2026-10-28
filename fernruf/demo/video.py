"""Portrait ffmpeg render of a demo recording."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from fernruf.demo.captions import transcript_to_srt

from fernruf.demo.phone_screen import (
    DEMO_CALL_LABEL,
    FPS,
    cues_from_srt,
    render_phone_frame,
)

DEMO_VIDEO_DISCLAIMER = DEMO_CALL_LABEL


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


def demo_video_ffmpeg_args(
    *,
    frames_pattern: str,
    wav: Path,
    mp4: Path,
    fps: int = FPS,
) -> list[str]:
    """Assemble pre-rendered phone-screen frames. No drawtext and no waveform."""
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-start_number",
        "0",
        "-i",
        frames_pattern,
        "-i",
        str(wav),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
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
            "ffmpeg is required for --video (libx264 and aac)"
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
        srt_text = transcript_to_srt(
            shift_turns(turns, offset),
            duration_ms=int(duration * 1000),
        )
        (work / "captions.srt").write_text(srt_text, encoding="utf-8")
        cues = cues_from_srt(srt_text)
        frames = work / "frames"
        frames.mkdir()
        count = max(1, int(round(duration * FPS)))
        for index in range(count):
            frame = render_phone_frame(index / FPS, cues, frame_index=index)
            frame.save(frames / f"{index:05d}.png")
        args = demo_video_ffmpeg_args(
            frames_pattern=str(frames / "%05d.png"),
            wav=source,
            mp4=mp4,
            fps=FPS,
        )
        completed = subprocess.run(args, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to render {mp4}: {detail[:500]}")
    if not mp4.is_file():
        raise RuntimeError(f"ffmpeg did not write {mp4}")
    return mp4
