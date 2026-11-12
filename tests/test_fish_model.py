"""FISH_AUDIO_MODEL is the call model unless FERNRUF_FISH_MODEL is set."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.settings import Settings
from fernruf.tts.audio import _write_m1_prompt


def test_fish_audio_model_wins_when_fernruf_fish_model_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FERNRUF_FISH_MODEL", raising=False)
    monkeypatch.setenv("FISH_AUDIO_MODEL", "drama-3")
    settings = Settings(_env_file=None)
    assert settings.fish_model == "s2.1-pro-free"
    assert settings.effective_fish_model() == "drama-3"


def test_fernruf_fish_model_wins_over_fish_audio_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FERNRUF_FISH_MODEL", "s2-pro")
    monkeypatch.setenv("FISH_AUDIO_MODEL", "drama-3")
    settings = Settings(_env_file=None)
    assert settings.effective_fish_model() == "s2-pro"


def test_fish_model_defaults_when_neither_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FERNRUF_FISH_MODEL", raising=False)
    monkeypatch.delenv("FISH_AUDIO_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.effective_fish_model() == "s2.1-pro-free"


def test_constructor_fish_model_wins_over_fish_audio_model() -> None:
    settings = Settings(
        _env_file=None,
        fish_model="s2-pro",
        fish_audio_model="drama-3",
    )
    assert settings.effective_fish_model() == "s2-pro"


def _capture_fish(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    seen: dict[str, str] = {}

    def fake(text: str, *, api_key: str, model: str, voice_id: str | None):
        del text, api_key, voice_id
        seen["model"] = model
        return b"\x00\x00" * 80, 8000

    monkeypatch.setattr("fernruf.tts.audio.fish_tts_pcm_rest", fake)
    monkeypatch.setattr("fernruf.tts.audio.fish_tts_pcm_ws", fake)
    return seen


def test_m1_prompt_prefers_fernruf_fish_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_fish(monkeypatch)
    monkeypatch.setenv("FERNRUF_FISH_MODEL", "s2-pro")
    settings = Settings(
        _env_file=None,
        fish_audio_api_key="test-key",
        fish_audio_model="drama-3",
    )
    label = _write_m1_prompt(settings, tmp_path / "prompt.alaw")
    assert label == "fish-rest"
    assert seen["model"] == "s2-pro"


def test_m1_prompt_uses_fish_audio_model_when_fernruf_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_fish(monkeypatch)
    monkeypatch.delenv("FERNRUF_FISH_MODEL", raising=False)
    settings = Settings(
        _env_file=None,
        fish_audio_api_key="test-key",
        fish_audio_model="drama-3",
    )
    label = _write_m1_prompt(settings, tmp_path / "prompt.alaw")
    assert label == "fish-rest"
    assert seen["model"] == "drama-3"


def test_fish_smoke_prefers_fernruf_fish_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stream(*args, **kwargs):
        del args, kwargs
        yield b"\x00\x00" * 160, 12.0

    monkeypatch.setattr("fernruf.tts.fish_rest_client.astream_fish_tts_pcm_rest", fake_stream)
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "test-key")
    monkeypatch.setenv("FISH_AUDIO_MODEL", "drama-3")
    monkeypatch.setenv("FERNRUF_FISH_MODEL", "s2-pro")
    result = CliRunner().invoke(main, ["fish-smoke", "Hallo"])
    assert result.exit_code == 0, result.output
    assert '"model": "s2-pro"' in result.output


def test_fish_smoke_reports_the_effective_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stream(*args, **kwargs):
        del args, kwargs
        yield b"\x00\x00" * 160, 12.0

    monkeypatch.setattr("fernruf.tts.fish_rest_client.astream_fish_tts_pcm_rest", fake_stream)
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "test-key")
    monkeypatch.setenv("FISH_AUDIO_MODEL", "drama-3")
    monkeypatch.delenv("FERNRUF_FISH_MODEL", raising=False)
    result = CliRunner().invoke(main, ["fish-smoke", "Hallo"])
    assert result.exit_code == 0, result.output
    assert '"model": "drama-3"' in result.output
