from __future__ import annotations

import asyncio

import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallError, CallManager
from fernruf.settings import Settings


def test_place_call_rejects_missing_llm_model(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        principal_name="Jordan Lee",
        llm_mock=False,
        llm_model="",
        voice_mock=True,
        m3_simulate=True,
        call_settings_db=str(tmp_path / "s.sqlite"),
        audit_path=str(tmp_path / "audit.jsonl"),
        latency_log_path=str(tmp_path / "lat.jsonl"),
        allowlist_numbers=["+493023125123"],
        quiet_hours="off",
        quiet_hours_allow_off=True,
    )
    manager = CallManager(settings, line_health=StaticLineHealth(True))
    with pytest.raises(CallError) as exc:
        asyncio.run(
            manager.place(to="+493023125123", task={"goal": "Test"}, wait=False)
        )
    assert exc.value.code == "llm_model_missing"


def test_m2_simulate_exits_cleanly_without_llm_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", "Jordan Lee")
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "+493023125123")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS", "off")
    monkeypatch.setenv("FERNRUF_LLM_MOCK", "false")
    monkeypatch.setenv("FERNRUF_VOICE_MOCK", "true")
    monkeypatch.setenv("FERNRUF_LLM_MODEL", "")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "fake-ari-password-16bytes")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "m2-test-call",
            "--simulate",
            "--to",
            "+493023125123",
            "--task",
            "Short test",
        ],
    )
    assert result.exit_code == 2
    assert "FERNRUF_LLM_MODEL" in result.output
    assert "Traceback" not in result.output
