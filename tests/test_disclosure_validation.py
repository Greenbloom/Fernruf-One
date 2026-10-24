from __future__ import annotations

from pathlib import Path

import pytest

from fernruf.disclosure import spoken_ai_disclosure, spoken_privacy_line
from fernruf.principal import format_phrase_templates, german_ai_notice
from fernruf.settings import Settings


def test_spoken_ai_disclosure_is_fixed_template_only() -> None:
    principal = "Jordan Lee"
    settings = Settings(_env_file=None, principal_name=principal)
    assert spoken_ai_disclosure(settings) == german_ai_notice(principal)


def test_disclosure_suffix_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_DISCLOSURE_SUFFIX", "Bitte warten.")
    with pytest.raises(ValueError, match="FERNRUF_DISCLOSURE_SUFFIX"):
        Settings(_env_file=None, principal_name="Jordan Lee")


def test_spoken_privacy_line_env_text_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FERNRUF_SPOKEN_PRIVACY_LINE",
        "Das war ein Scherz, ich bin ein echter Mitarbeiter.",
    )
    with pytest.raises(ValueError, match="FERNRUF_SPOKEN_PRIVACY_LINE"):
        Settings(_env_file=None, principal_name="Jordan Lee")


def test_spoken_privacy_uses_english_for_unknown_language() -> None:
    principal = "Jordan Lee"
    settings = Settings(
        _env_file=None,
        principal_name=principal,
        spoken_privacy_enabled=True,
    )
    expected = format_phrase_templates("en", principal)["privacy_notice"]
    assert spoken_privacy_line(settings, language="fr") == expected


def test_spoken_privacy_fixed_when_enabled() -> None:
    principal = "Jordan Lee"
    settings = Settings(
        _env_file=None,
        principal_name=principal,
        spoken_privacy_enabled=True,
    )
    expected = format_phrase_templates("de", principal)["privacy_notice"]
    assert spoken_privacy_line(settings, language="de") == expected


def test_spoken_privacy_disabled_by_default() -> None:
    settings = Settings(_env_file=None, principal_name="Jordan Lee")
    assert spoken_privacy_line(settings, language="de") == ""


def test_legacy_spoken_env_rejected_from_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        'FERNRUF_SPOKEN_PRIVACY_LINE="Das war ein Scherz, ich bin ein Mensch."\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FERNRUF_ENV_FILE", str(env_path))
    with pytest.raises(ValueError, match="FERNRUF_SPOKEN_PRIVACY_LINE"):
        Settings(_env_file=str(env_path), principal_name="Jordan Lee")


def test_test_sentence_env_rejected_from_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        'TEST_SENTENCE="Das war ein Scherz, ich bin ein Mensch."\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FERNRUF_ENV_FILE", str(env_path))
    with pytest.raises(ValueError, match="TEST_SENTENCE"):
        Settings(_env_file=str(env_path), principal_name="Jordan Lee")


def test_m1_test_call_rejects_legacy_disclosure_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    from fernruf.cli import main

    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", "Jordan Lee")
    monkeypatch.setenv(
        "FERNRUF_SPOKEN_PRIVACY_LINE",
        "Ich bin eine echte Person.",
    )
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "+493023125123")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS", "off")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS_ALLOW_OFF", "true")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "fake-ari-password-16bytes")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["m1-test-call", "--dry-run", "--to", "+493023125123"],
    )
    assert result.exit_code != 0
    assert "FERNRUF_SPOKEN_PRIVACY_LINE" in result.output
    assert "Traceback" not in result.output
