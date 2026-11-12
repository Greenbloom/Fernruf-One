"""Local listen preference. Missing file means listening is off."""

from __future__ import annotations

import json
from pathlib import Path


def default_pref_path() -> Path:
    return Path.home() / ".config" / "fernruf" / "listen.json"


def load_listen_pref(path: Path) -> dict:
    off = {"listen": False, "volume": 0.8, "muted": False}
    if not path.is_file():
        return off
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return off
    if not isinstance(raw, dict):
        return off
    volume = raw.get("volume", 0.8)
    try:
        volume_f = float(volume)
    except (TypeError, ValueError):
        volume_f = 0.8
    volume_f = min(1.0, max(0.0, volume_f))
    return {
        "listen": bool(raw.get("listen")),
        "volume": volume_f,
        "muted": bool(raw.get("muted")),
    }


def save_listen_pref(path: Path, *, listen: bool, volume: float, muted: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "listen": bool(listen),
        "volume": min(1.0, max(0.0, float(volume))),
        "muted": bool(muted),
    }
    path.write_text(json.dumps(body), encoding="utf-8")
