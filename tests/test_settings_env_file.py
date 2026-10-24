import os
from pathlib import Path

import pytest

from fernruf.settings import Settings, load_settings


def test_load_settings_fernruf_env_file_none_ignores_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("FERNRUF_QUIET_HOURS=off\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.delenv("FERNRUF_QUIET_HOURS", raising=False)
    s = load_settings()
    assert s.quiet_hours == "20:00-08:00"


def test_hangup_margin_ms_overrides_legacy_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_HANGUP_MARGIN_MS", "700")
    monkeypatch.setenv("FERNRUF_HANGUP_AFTER_BOT_STOPPED_MARGIN_S", "0.4")
    settings = Settings(_env_file=None)
    assert settings.hangup_margin_s == 0.7


def test_hangup_margin_defaults_to_700ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FERNRUF_HANGUP_MARGIN_MS", raising=False)
    monkeypatch.delenv("FERNRUF_HANGUP_AFTER_BOT_STOPPED_MARGIN_S", raising=False)
    settings = Settings(_env_file=None)
    assert settings.hangup_margin_ms is None
    assert settings.hangup_margin_s == 0.7


def test_load_settings_fernruf_env_file_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    verify = tmp_path / ".env.verify"
    verify.write_text(
        "FERNRUF_QUIET_HOURS=off\nFERNRUF_QUIET_HOURS_ALLOW_OFF=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FERNRUF_ENV_FILE", str(verify))
    monkeypatch.delenv("FERNRUF_QUIET_HOURS", raising=False)
    s = load_settings()
    assert s.quiet_hours == "off"
