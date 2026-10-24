from __future__ import annotations

from fernruf.tts.fish_clause import split_first_clause


def test_en_dash_does_not_split_das_ist_sentence() -> None:
    text = (
        "Das ist ein kurzer Testanruf – ist die Verbindung bei Ihnen auch gut verständlich?"
    )
    first, rest = split_first_clause(text)
    assert first is None
    assert rest == text


def test_comma_splits_first_clause() -> None:
    text = "Hallo, dies ist ein kurzer Testanruf zur Verbindungsprüfung."
    first, rest = split_first_clause(text, min_words=1)
    assert first == "Hallo,"
    assert rest.startswith("dies ist")
