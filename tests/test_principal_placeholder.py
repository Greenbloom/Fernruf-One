"""Refuse real dials while FERNRUF_PRINCIPAL_NAME is empty or the .env.example placeholder."""

from __future__ import annotations

import asyncio

import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.principal import PRINCIPAL_ENV_PLACEHOLDER, PRINCIPAL_PLACEHOLDER_MESSAGE
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallError, CallManager
from tests.m3_support import service_settings


def test_placeholder_constant_is_documented_example() -> None:
    assert PRINCIPAL_ENV_PLACEHOLDER == "Alex Example"


def test_manager_rejects_placeholder_for_live_dial(tmp_path) -> None:
    settings, _token = service_settings(
        tmp_path,
        FERNRUF_PRINCIPAL_NAME=PRINCIPAL_ENV_PLACEHOLDER,
        FERNRUF_VOICE_MOCK=False,
        FERNRUF_LLM_MOCK=False,
        FERNRUF_M3_SIMULATE=False,
    )
    manager = CallManager(settings, line_health=StaticLineHealth(True))

    with pytest.raises(CallError) as exc:
        asyncio.run(
            manager.place(to="+493023125123", task={"goal": "Test"}, wait=False)
        )
    assert exc.value.code == "principal_placeholder"
    assert "FERNRUF_PRINCIPAL_NAME" in exc.value.message
    assert PRINCIPAL_PLACEHOLDER_MESSAGE in exc.value.message


def test_manager_rejects_placeholder_when_only_llm_mock(tmp_path) -> None:
    settings, _token = service_settings(
        tmp_path,
        FERNRUF_PRINCIPAL_NAME=PRINCIPAL_ENV_PLACEHOLDER,
        FERNRUF_VOICE_MOCK=False,
        FERNRUF_LLM_MOCK=True,
        FERNRUF_M3_SIMULATE=False,
    )
    manager = CallManager(settings, line_health=StaticLineHealth(True))
    with pytest.raises(CallError) as exc:
        asyncio.run(
            manager.place(to="+493023125123", task={"goal": "Test"}, wait=False)
        )
    assert exc.value.code == "principal_placeholder"


def test_manager_allows_placeholder_in_mock_mode(tmp_path) -> None:
    settings, _token = service_settings(
        tmp_path,
        principal_name=PRINCIPAL_ENV_PLACEHOLDER,
        voice_mock=True,
        llm_mock=True,
        m3_simulate=True,
    )
    manager = CallManager(settings, line_health=StaticLineHealth(True))

    async def run():
        return await manager.place(to="+493023125123", task={"goal": "Test"}, wait=False)

    queued = asyncio.run(run())
    assert queued["status"] == "queued"


def test_m2_test_call_simulate_allows_placeholder(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", PRINCIPAL_ENV_PLACEHOLDER)
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "+493023125123")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS", "off")
    monkeypatch.setenv("FERNRUF_LLM_MOCK", "true")
    monkeypatch.setenv("FERNRUF_VOICE_MOCK", "true")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "fake-ari-password-16bytes")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-token-value")

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
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output


def test_m2_test_call_live_rejects_placeholder(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", PRINCIPAL_ENV_PLACEHOLDER)
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "+493023125123")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS", "off")
    monkeypatch.setenv("FERNRUF_VOICE_MOCK", "false")
    monkeypatch.setenv("FERNRUF_LLM_MOCK", "false")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "m2-test-call",
            "--to",
            "+493023125123",
            "--task",
            "Short test",
        ],
    )
    assert result.exit_code == 2
    assert "FERNRUF_PRINCIPAL_NAME" in result.output


def test_placeholder_match_is_case_insensitive() -> None:
    from fernruf.principal import principal_name_is_placeholder

    assert principal_name_is_placeholder("  alex example  ")
    assert principal_name_is_placeholder("")
    assert not principal_name_is_placeholder("Jordan Lee")
