from __future__ import annotations

from pathlib import Path

from fernruf.settings import Settings
from fernruf.tts.audio import _m1_spoken_text, prepare_m1_prompt


def test_m1_spoken_text_includes_ai_disclosure(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        principal_name="Jordan Lee",
        sounds_dir=str(tmp_path / "sounds"),
    )
    text = _m1_spoken_text(settings)
    assert "Jordan Lee" in text
    assert "KI-Assistent" in text


def test_prepare_m1_prompt_writes_audio_with_disclosure(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        principal_name="Erika Muster",
        sounds_dir=str(tmp_path / "sounds"),
        fish_audio_api_key="",
    )
    source, media = prepare_m1_prompt(settings)
    assert source == "fixture"
    assert media.startswith("sound:custom/m1-prompt-")
    assert _m1_spoken_text(settings).startswith("Hallo")
