"""SQLite rows for calls, transcript lines, and spend. Same file as call settings."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fernruf.quiet_hours import BERLIN
from fernruf.settings import Settings

_LOCK = threading.Lock()


def _connect(settings: Settings) -> sqlite3.Connection:
    path = Path(settings.call_settings_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY,
            json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhooks (
            webhook_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            events TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def upsert_call(settings: Settings, call_id: str, **fields: Any) -> dict[str, Any]:
    with _LOCK:
        conn = _connect(settings)
        try:
            row = conn.execute("SELECT json FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            current: dict[str, Any] = json.loads(row["json"]) if row else {}
            current.update(fields)
            current["call_id"] = call_id
            conn.execute(
                "INSERT INTO calls (call_id, json) VALUES (?, ?) "
                "ON CONFLICT(call_id) DO UPDATE SET json = excluded.json",
                (call_id, json.dumps(current, ensure_ascii=False)),
            )
            conn.commit()
            return current
        finally:
            conn.close()


def get_stored_call(settings: Settings, call_id: str) -> dict[str, Any] | None:
    path = Path(settings.call_settings_db)
    if not path.is_file():
        return None
    with _LOCK:
        conn = _connect(settings)
        try:
            row = conn.execute("SELECT json FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            if not row:
                return None
            data = json.loads(row["json"])
            return data if isinstance(data, dict) else None
        finally:
            conn.close()


def list_stored_calls(settings: Settings) -> list[dict[str, Any]]:
    path = Path(settings.call_settings_db)
    if not path.is_file():
        return []
    with _LOCK:
        conn = _connect(settings)
        try:
            rows = conn.execute("SELECT json FROM calls").fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                data = json.loads(row["json"])
                if isinstance(data, dict):
                    out.append(data)
            return out
        finally:
            conn.close()


def _parse_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BERLIN)
    return parsed


def spend_snapshot(settings: Settings, *, now: datetime) -> dict[str, float]:
    """Caps for the Berlin calendar day and calendar month.

    Only rows with ``counts_toward_caps`` are included, so a policy rejection
    does not burn the daily or monthly allowance. An in-progress call reserves
    its ``reserved_minutes``; a finalized call counts its real duration.
    """
    day = now.astimezone(BERLIN).date()
    month = (day.year, day.month)
    calls_today = 0
    minutes_today = 0.0
    spent = 0.0
    mobile = 0.0
    for row in list_stored_calls(settings):
        if not row.get("counts_toward_caps", False):
            continue
        started = _parse_at(row.get("started_at"))
        if started is None:
            continue
        local = started.astimezone(BERLIN)
        if (local.year, local.month) == month:
            spent += float(row.get("cost_estimate_eur") or 0)
            mobile += float(row.get("mobile_cost_eur") or 0)
        if local.date() == day:
            calls_today += 1
            if row.get("status") == "finalized":
                minutes_today += float(row.get("duration_s") or 0) / 60.0
            else:
                minutes_today += float(row.get("reserved_minutes") or 0)
    return {
        "calls_today": float(calls_today),
        "minutes_today": minutes_today,
        "spent_eur": round(spent, 4),
        "mobile_spent_eur": round(mobile, 4),
    }


def save_webhook(
    settings: Settings,
    *,
    webhook_id: str,
    url: str,
    events: list[str],
    created_at: str,
) -> None:
    with _LOCK:
        conn = _connect(settings)
        try:
            conn.execute(
                "INSERT INTO webhooks (webhook_id, url, events, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(webhook_id) DO UPDATE SET url = excluded.url, "
                "events = excluded.events, created_at = excluded.created_at",
                (webhook_id, url, json.dumps(events), created_at),
            )
            conn.commit()
        finally:
            conn.close()
