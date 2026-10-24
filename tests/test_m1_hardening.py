from __future__ import annotations

import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from fernruf.cli import main
from fernruf.logging_config import configure_logging
from fernruf.settings import Settings, load_settings, validate_ari_password
from fernruf.tts.audio import prepare_m1_prompt


FAKE_FISH_KEY = "fakefishkey1234567890"
FAKE_ARI_PASS = "fake-ari-password-16bytes"


def test_pjsip_and_ari_caller_id_defaults() -> None:
    s = Settings(
        FERNRUF_CALLER_ID_NAME="Fernruf",
        FERNRUF_CALLER_ID_NUMBER="620",
    )
    assert s.pjsip_callerid == '"Fernruf" <620>'
    assert s.ari_caller_id == s.pjsip_callerid


@pytest.mark.parametrize(
    "password",
    ["", "change-me-ari-secret", "fernruf-dev"],
)
def test_validate_ari_password_rejects_placeholders(password: str) -> None:
    with pytest.raises(SystemExit, match="ASTERISK_ARI_PASSWORD"):
        validate_ari_password(password)


def test_verbose_logging_never_prints_fake_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(
        True,
        secrets=(FAKE_FISH_KEY, FAKE_ARI_PASS),
    )
    log = logging.getLogger("fernruf")
    log.info("Authorization: Bearer %s", FAKE_FISH_KEY)
    log.info("connect ws://127.0.0.1:8088/ari/events?api_key=fernruf:%s", FAKE_ARI_PASS)
    log.debug("header Authorization: Bearer %s", FAKE_FISH_KEY)

    combined = capsys.readouterr().err
    assert FAKE_FISH_KEY not in combined
    assert FAKE_ARI_PASS not in combined
    assert "Authorization: [REDACTED]" in combined
    assert "api_key=[REDACTED]" in combined


def test_prepare_m1_prompt_drops_stale_sibling_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sounds = tmp_path
    (sounds / "m1-prompt.wav").write_bytes(b"stale-wav-fixture")
    (sounds / "m1-prompt.alaw").write_bytes(b"stale-alaw")
    monkeypatch.setenv("FERNRUF_SOUNDS_DIR", str(sounds))
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "")
    settings = load_settings()
    source, media = prepare_m1_prompt(settings)
    assert source == "fixture"
    assert media.startswith("sound:custom/m1-prompt-")
    assert not (sounds / "m1-prompt.wav").exists()
    assert not (sounds / "m1-prompt.alaw").exists()
    alaw_files = list(sounds.glob("m1-prompt-*.alaw"))
    assert len(alaw_files) == 1
    assert media == f"sound:custom/{alaw_files[0].stem}"


def test_cli_verbose_does_not_leak_env_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FISH_AUDIO_API_KEY", FAKE_FISH_KEY)
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", FAKE_ARI_PASS)
    runner = CliRunner()
    result = runner.invoke(main, ["-v", "policy-check", "+493023125123"], catch_exceptions=False)
    assert result.exit_code in (0, 2)
    assert FAKE_FISH_KEY not in result.output
    assert FAKE_ARI_PASS not in result.output
