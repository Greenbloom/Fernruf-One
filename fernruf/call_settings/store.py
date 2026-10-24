from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from fernruf.call_settings.model import validate_llm_model_id
from fernruf.settings import Settings, load_settings

DEFAULT_DB = "data/fernruf.sqlite"


@dataclass
class CallSettingsRecord:
    """Shape aligned with SPEC §3 get_call_settings (M3/M5)."""

    voice_reference_id: str = ""
    persona_name: str = ""
    opening_line: str = ""
    default_language: str = "de"
    llm_model: str = ""
    llm_model_user_set: bool = False
    max_minutes: int = 10
    monthly_budget_eur: float = 25.0
    mobile_flat_enabled: bool = False
    allowlist_enabled: bool = True
    allowlist: list[str] = field(default_factory=list)
    international_enabled: bool = False
    quiet_hours: str = "20:00-08:00"
    updated_at: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _db_path(settings: Settings) -> Path:
    return Path(settings.call_settings_db)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS call_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            json TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _defaults_from_env(settings: Settings) -> CallSettingsRecord:
    return CallSettingsRecord(
        opening_line=settings.disclosure_prefix,
        llm_model=settings.llm_model,
        max_minutes=settings.max_minutes,
        monthly_budget_eur=settings.monthly_budget_eur,
        mobile_flat_enabled=settings.mobile_flat_enabled,
        allowlist_enabled=True,
        allowlist=sorted(settings.allowlist_numbers),
        international_enabled=settings.international_enabled,
        quiet_hours=settings.quiet_hours,
    )


def _record_from_json(data: dict[str, Any]) -> CallSettingsRecord:
    names = {f.name for f in fields(CallSettingsRecord)}
    return CallSettingsRecord(**{k: data[k] for k in names if k in data})


def get_call_settings(settings: Settings | None = None) -> CallSettingsRecord:
    s = settings or load_settings()
    path = _db_path(s)
    conn = _connect(path)
    try:
        _init_schema(conn)
        row = conn.execute("SELECT json FROM call_settings WHERE id = 1").fetchone()
        if not row:
            rec = _defaults_from_env(s)
            _save(conn, rec)
            return rec
        rec = _record_from_json(json.loads(row["json"]))
        if not rec.llm_model.strip():
            rec.llm_model = s.llm_model
        return rec
    finally:
        conn.close()


def _save(conn: sqlite3.Connection, rec: CallSettingsRecord) -> None:
    rec.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = json.dumps(asdict(rec), ensure_ascii=False)
    conn.execute(
        "INSERT INTO call_settings (id, json) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET json = excluded.json",
        (payload,),
    )
    conn.commit()


def update_call_settings(
    patch: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> CallSettingsRecord:
    s = settings or load_settings()
    path = _db_path(s)
    conn = _connect(path)
    try:
        _init_schema(conn)
        row = conn.execute("SELECT json FROM call_settings WHERE id = 1").fetchone()
        if row:
            current = _record_from_json(json.loads(row["json"]))
        else:
            current = _defaults_from_env(s)
        for key, val in patch.items():
            if key not in {f.name for f in fields(CallSettingsRecord)}:
                raise ValueError(f"unknown setting: {key}")
            if key == "llm_model" and val is not None:
                val = validate_llm_model_id(str(val))
                current.llm_model_user_set = True
            if key == "allowlist" and val is not None:
                val = list(val)
            setattr(current, key, val)
        _save(conn, current)
        return current
    finally:
        conn.close()
