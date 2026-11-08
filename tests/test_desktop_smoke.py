"""The placeholder window loads without a display."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")


def test_placeholder_window_starts_offscreen() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    proc = subprocess.run(
        [sys.executable, "-m", "fernruf.desktop", "--smoke"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
