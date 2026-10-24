"""Persisted call rows for a later ``get_call``.

M3 will fill status, summary, and transcript fields. ``call_language`` is stored
as soon as the language is chosen, before the call is dialed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fernruf.settings import Settings, load_settings


def _db_path(settings: Settings) -> Path:
    return Path(settings.call_settings_db)


def _connect(settings: Settings) -> sqlite3.Connection:
    path = _db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY,
            json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def remember_call(
    settings: Settings,
    *,
    call_id: str,
    call_language: str,
    task: str = "",
    to_e164: str = "",
    status: str = "dialing",
) -> dict[str, Any]:
    """Insert or merge a call row. ``call_language`` is the language spoken on the call."""
    conn = _connect(settings)
    try:
        row = conn.execute("SELECT json FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        current: dict[str, Any] = json.loads(row["json"]) if row else {}
        now = datetime.now(tz=timezone.utc).isoformat()
        current.setdefault("call_id", call_id)
        current.setdefault("started_at", now)
        current["call_language"] = (call_language or "de").strip().lower() or "de"
        if task:
            current["task"] = task
        if to_e164:
            current["to"] = to_e164
        if status:
            # The simulate/live path stamps "simulated" while a service call is
            # already in a real lifecycle state. Keep the lifecycle status.
            existing = current.get("status")
            if not (status == "simulated" and existing in {
                "queued",
                "dialing",
                "ringing",
                "answered",
                "finalized",
            }):
                current["status"] = status
        payload = json.dumps(current, ensure_ascii=False)
        conn.execute(
            "INSERT INTO calls (call_id, json) VALUES (?, ?) "
            "ON CONFLICT(call_id) DO UPDATE SET json = excluded.json",
            (call_id, payload),
        )
        conn.commit()
        return current
    finally:
        conn.close()


def get_call(call_id: str, *, settings: Settings | None = None) -> dict[str, Any] | None:
    """Return the stored call record, including ``call_language``, or None."""
    s = settings or load_settings()
    path = _db_path(s)
    if not path.is_file():
        return None
    conn = _connect(s)
    try:
        row = conn.execute("SELECT json FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        if not row:
            return None
        data = json.loads(row["json"])
        if not isinstance(data, dict):
            return None
        return data
    finally:
        conn.close()
