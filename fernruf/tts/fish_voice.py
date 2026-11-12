from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.tts.fish_voice")


def fish_reference_id(settings: Settings, explicit: str | None = None) -> str:
    for candidate in (
        getattr(settings, "fish_voice_id", ""),
        explicit,
        getattr(settings, "_pinned_fish_voice_id", ""),
        getattr(settings, "_operator_fish_voice_id", ""),
    ):
        voice = str(candidate or "").strip()
        if voice:
            return voice
    cached = getattr(settings, "_stored_voice_reference_id", None)
    if isinstance(cached, str):
        return cached
    stored = _stored_voice_reference_id(settings)
    object.__setattr__(settings, "_stored_voice_reference_id", stored)
    return stored


def _stored_voice_reference_id(settings: Settings) -> str:
    path = Path(settings.call_settings_db)
    if not path.is_file():
        return ""
    try:
        from fernruf.call_settings.store import get_call_settings

        record = get_call_settings(settings)
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError):
        LOG.warning("Could not read voice_reference_id", exc_info=True)
        return ""
    return (record.voice_reference_id or "").strip()
