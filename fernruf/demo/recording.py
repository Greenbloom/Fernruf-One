"""Copy the Asterisk mix onto the demo output directory.

The dialplan MixMonitor can stop when the Local channel is bridged. The ARI
bridge recording keeps going until hangup. The file we publish is the longer
of the two, and only after it covers the last spoken line.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import wave
from pathlib import Path

from fernruf.data_dir import DATA_DIR_FIX
from fernruf.demo.plan import DemoCallPlan


def recording_missing_message(path: Path) -> str:
    return (
        f"Mixed call audio was not written to {path}. "
        "demo-call records with MixMonitor on the local Asterisk extension "
        "and with an ARI bridge recording in the same directory. "
        "Create the bind-mount directory before starting Asterisk "
        f"({DATA_DIR_FIX}) and recreate the container so it mounts "
        "./data/asterisk-monitor and loads the fernruf-demo dialplan."
    )


def _wav_layout(path: Path) -> tuple[int, int, int, int] | None:
    """Channels, sample width, rate, and the frame count stored in the header.

    The header is read as bytes. ``wave`` stops at a RIFF size of 0, which is
    how an Asterisk recording looks before it is closed.
    """
    try:
        with path.open("rb") as handle:
            blob = handle.read(44)
    except OSError:
        return None
    if len(blob) >= 44 and blob[0:4] == b"RIFF" and blob[8:12] == b"WAVE":
        channels = int.from_bytes(blob[22:24], "little")
        rate = int.from_bytes(blob[24:28], "little")
        bits = int.from_bytes(blob[34:36], "little")
        data_size = int.from_bytes(blob[40:44], "little")
        if channels > 0 and rate > 0 and bits in (8, 16, 24, 32):
            width = bits // 8
            if data_size in (0, 0xFFFFFFFF):
                frames = 0
            else:
                frames = data_size // (channels * width)
            return channels, width, rate, frames
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            if rate <= 0 or channels <= 0 or width <= 0:
                return None
            return channels, width, rate, handle.getnframes()
    except (wave.Error, EOFError, OSError):
        return None


def wav_duration_seconds(path: Path) -> float | None:
    """Seconds of audio, including bytes past a header Asterisk has not closed.

    MixMonitor and bridge recordings leave the WAV length at 0 until stop.
    The payload is the real length in that window.
    """
    layout = _wav_layout(path)
    if layout is None:
        return None
    channels, width, rate, frames = layout
    header = frames / float(rate)
    try:
        size = path.stat().st_size
    except OSError:
        return header
    payload = max(0, size - 44) / float(rate * channels * width)
    if header + 0.05 < payload:
        return payload
    return header


def _copy_recording(src: Path, dest: Path) -> None:
    """Copy ``src``. Repair a header that still says the file is shorter than its payload."""
    layout = _wav_layout(src)
    if layout is None:
        shutil.copyfile(src, dest)
        return
    channels, width, rate, frames = layout
    header = frames / float(rate)
    payload_bytes = max(0, src.stat().st_size - 44)
    frame_bytes = channels * width
    payload = payload_bytes / float(rate * frame_bytes)
    if header + 0.05 >= payload:
        shutil.copyfile(src, dest)
        return
    raw = src.read_bytes()[44:]
    raw = raw[: len(raw) - (len(raw) % frame_bytes)]
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(raw)


def recording_sources(plan: DemoCallPlan) -> list[Path]:
    paths = [plan.monitor_host_path]
    extra = plan.bridge_recording_host_path
    if extra is not None and extra != plan.monitor_host_path:
        paths.append(extra)
    return paths


def ready_recording(
    sources: list[Path],
    stable: dict[Path, int],
    *,
    min_seconds: float,
) -> Path | None:
    """Longest finalized wav, once it reaches ``min_seconds``.

    A file that is still growing can become the longest. Do not publish a
    shorter stable file while that is happening.
    """
    ranked: list[tuple[float, Path]] = []
    growing = 0.0
    for source in sources:
        if not source.is_file():
            continue
        dur = wav_duration_seconds(source)
        if dur is None:
            continue
        if stable.get(source, 0) >= 2:
            ranked.append((dur, source))
        else:
            growing = max(growing, dur)
    if not ranked:
        return None
    dur, path = max(ranked)
    if growing > dur + 0.05:
        return None
    if min_seconds > 0 and dur + 0.05 < min_seconds:
        return None
    return path


async def publish_recording(
    plan: DemoCallPlan,
    *,
    min_seconds: float = 0.0,
    timeout_s: float = 20.0,
) -> Path:
    sources = recording_sources(plan)
    dest = plan.out / "call.wav"
    deadline = time.monotonic() + timeout_s
    last_size: dict[Path, int] = {}
    stable: dict[Path, int] = {}
    while time.monotonic() < deadline:
        for source in sources:
            if not source.is_file():
                continue
            size = source.stat().st_size
            if size > 44 and size == last_size.get(source):
                stable[source] = stable.get(source, 0) + 1
            else:
                stable[source] = 0
            last_size[source] = size
        chosen = ready_recording(sources, stable, min_seconds=min_seconds)
        if chosen is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_recording(chosen, dest)
            return dest
        await asyncio.sleep(0.25)
    detail = _short_detail(sources, min_seconds)
    raise FileNotFoundError(recording_missing_message(plan.monitor_host_path) + detail)


def _short_detail(sources: list[Path], min_seconds: float) -> str:
    parts: list[str] = []
    for source in sources:
        dur = wav_duration_seconds(source) if source.is_file() else None
        if dur is None:
            parts.append(f"{source.name} missing")
        else:
            parts.append(f"{source.name} {dur:.1f}s")
    need = f" Need at least {min_seconds:.1f}s to include the last spoken line."
    return " " + ", ".join(parts) + "." + (need if min_seconds > 0 else "")
