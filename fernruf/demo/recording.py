"""Copy the Asterisk MixMonitor wav onto the demo output directory."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from fernruf.data_dir import DATA_DIR_FIX
from fernruf.demo.plan import DemoCallPlan


def recording_missing_message(path: Path) -> str:
    return (
        f"Mixed call audio was not written to {path}. "
        "demo-call records with MixMonitor on the local Asterisk extension. "
        "Create the bind-mount directory before starting Asterisk "
        f"({DATA_DIR_FIX}) and recreate the container so it mounts "
        "./data/asterisk-monitor and loads the fernruf-demo dialplan."
    )


async def publish_recording(plan: DemoCallPlan, *, timeout_s: float = 10.0) -> Path:
    source = plan.monitor_host_path
    dest = plan.out / "call.wav"
    deadline = time.monotonic() + timeout_s
    last = -1
    stable = 0
    while time.monotonic() < deadline:
        if source.is_file():
            size = source.stat().st_size
            if size > 44 and size == last:
                stable += 1
                if stable >= 2:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
                    return dest
            else:
                stable = 0
            last = size
        await asyncio.sleep(0.25)
    raise FileNotFoundError(recording_missing_message(source))
