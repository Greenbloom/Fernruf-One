"""faster-whisper silence phrases from synthetic call scenario."""

from __future__ import annotations

from fernruf.settings import Settings
from fernruf.stt.whisper import (
    _transcribe_kwargs,
    is_lone_hallucination,
    stt_drop_reason,
    whisper_language_code,
)


def _ready(language: str) -> Settings:
    settings = Settings(_env_file=None, call_language=language)
    object.__setattr__(settings, "_language_ready", True)
    return settings


def test_transcribe_kwargs_follow_call_language_and_disable_prompt_conditioning() -> None:
    kw = _transcribe_kwargs(_ready("en"))
    assert kw["language"] == "en"
    assert kw["vad_filter"] is True
    assert kw["condition_on_previous_text"] is False
    assert whisper_language_code(_ready("en")) == "en"
    assert _transcribe_kwargs(_ready("de"))["language"] == "de"


def test_low_confidence_and_lone_hallucinations_are_dropped() -> None:
    assert stt_drop_reason("You want.", no_speech_prob=0.9, avg_logprob=-0.2) == "no_speech_prob"
    assert stt_drop_reason("How has your day been?", no_speech_prob=0.1, avg_logprob=-1.4) == (
        "avg_logprob"
    )
    assert stt_drop_reason("Vielen Dank.", no_speech_prob=0.8, avg_logprob=-0.3) == (
        "hallucination_phrase"
    )
    assert stt_drop_reason("Vielen Dank. Vielen Dank.", no_speech_prob=0.2, avg_logprob=-1.2) == (
        "hallucination_phrase"
    )
    assert stt_drop_reason("Untertitel im Auftrag des ZDF", no_speech_prob=0.7, avg_logprob=-0.2) == (
        "hallucination_phrase"
    )
    assert stt_drop_reason("Thanks for watching.", no_speech_prob=0.61, avg_logprob=-0.1) == (
        "hallucination_phrase"
    )
    assert is_lone_hallucination("Vielen Dank. Vielen Dank.")
    assert is_lone_hallucination("Thanks for watching!") is True


def test_confident_thanks_and_real_speech_are_kept() -> None:
    assert stt_drop_reason("Vielen Dank.", no_speech_prob=0.1, avg_logprob=-0.3) == ""
    assert stt_drop_reason("You want.", no_speech_prob=0.2, avg_logprob=-0.4) == ""
    assert stt_drop_reason(
        "Vielen Dank. Guten Abend. Und dann will ich das auch.",
        no_speech_prob=0.2,
        avg_logprob=-0.4,
    ) == ""
    assert is_lone_hallucination("Vielen Dank. Guten Abend.") is False


def test_thresholds_are_configurable() -> None:
    assert (
        stt_drop_reason(
            "hello there",
            no_speech_prob=0.4,
            avg_logprob=-0.5,
            no_speech_threshold=0.3,
            min_avg_logprob=-1.0,
        )
        == "no_speech_prob"
    )
    settings = Settings(_env_file=None, whisper_no_speech_prob=0.6, whisper_min_avg_logprob=-1.0)
    assert settings.whisper_no_speech_prob == 0.6
    assert settings.whisper_min_avg_logprob == -1.0
