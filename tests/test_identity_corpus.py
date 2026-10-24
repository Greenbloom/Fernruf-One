from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fernruf.principal import format_phrase_templates
from fernruf.settings import Settings
from fernruf.tts.audio import M1_TEST_SENTENCE, _m1_spoken_text
from fernruf.disclosure import spoken_ai_disclosure
from fernruf.voice.identity import scrub_identity_text

_CORPUS = Path(__file__).with_name("identity_corpus.tsv")
_PRINCIPAL = "Jordan Lee"
_PHRASES = format_phrase_templates("de", _PRINCIPAL)
_DE = format_phrase_templates("de", _PRINCIPAL)["identity_line"]
_EN = format_phrase_templates("en", _PRINCIPAL)["identity_line"]
_ZH = format_phrase_templates("zh", _PRINCIPAL)["identity_line"]


def _expand(text: str) -> str:
    return (
        text.replace("{P}", _PRINCIPAL)
        .replace("{DE}", _DE)
        .replace("{EN}", _EN)
        .replace("{ZH}", _ZH)
    )


def _load_corpus() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    with _CORPUS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            inp = _expand(row["input"])
            expect = row["expect_rewrite"].strip().lower() in ("yes", "true", "1")
            expected = _expand(row["expected_output"])
            rows.append((inp, expect, expected))
    return rows


@pytest.mark.parametrize(("text", "expect_rewrite", "expected"), _load_corpus())
def test_identity_corpus(text: str, expect_rewrite: bool, expected: str) -> None:
    cleaned, changed = scrub_identity_text(text, principal=_PRINCIPAL)
    assert changed is expect_rewrite, f"changed={changed} got={cleaned!r}"
    assert cleaned == expected, f"expected={expected!r} got={cleaned!r}"


def test_m1_opening_is_fixed_and_not_overridden() -> None:
    settings = Settings(_env_file=None, principal_name=_PRINCIPAL)
    spoken = _m1_spoken_text(settings)
    assert spoken == f"{spoken_ai_disclosure(settings)} {M1_TEST_SENTENCE}".strip()
    assert "KI-Assistent" in spoken
