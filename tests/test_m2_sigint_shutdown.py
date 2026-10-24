from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path


def test_sigint_mid_pipecat_exits_without_event_loop_traceback() -> None:
    child = Path(__file__).resolve().parent / "sigint_pipecat_child.py"
    proc = subprocess.Popen(
        [sys.executable, str(child)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if b"READY" in line:
                break
        else:
            proc.kill()
            raise AssertionError("child did not print READY before timeout")
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    combined = (stdout + stderr).decode("utf-8", errors="replace").lower()
    assert proc.returncode == 0, combined[-2000:]
    assert "no running event loop" not in combined
    assert "task was destroyed but it is pending" not in combined
