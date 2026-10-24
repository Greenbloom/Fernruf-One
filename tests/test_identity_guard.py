from __future__ import annotations

import asyncio
import logging

import pytest
from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.principal import format_phrase_templates
from fernruf.voice.identity import scrub_identity_text
from fernruf.voice.processors.identity_guard import IdentityOutputGuardProcessor
from fernruf.voice.prompt import build_system_prompt
from fernruf.settings import Settings
from tests.conftest import PRINCIPAL_TEST_NAMES

_PRINCIPAL = "Jordan Lee"


def _de_line(principal: str = _PRINCIPAL) -> str:
    return format_phrase_templates("de", principal)["identity_line"]


def _en_line(principal: str = _PRINCIPAL) -> str:
    return format_phrase_templates("en", principal)["identity_line"]


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_prompt_forbids_principal_self_identification(
    principal_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", principal_name)
    prompt = build_system_prompt(
        Settings(_env_file=None, principal_name=principal_name),
        "Kurzer Test",
    )
    lower = prompt.lower()
    assert "ki-assistent" in lower
    assert f"nicht {principal_name.lower()}" in lower
    assert f"ich bin ein ki-assistent von {principal_name.lower()}" in lower


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_scrub_identity_rewrites_forbidden_phrases(principal_name: str) -> None:
    first = principal_name.split()[0]
    de = format_phrase_templates("de", principal_name)["identity_line"]
    for text in (
        f"Mein Name ist {principal_name} – ich rufe an.",
        f"Ich bin {first} und helfe Ihnen gern.",
        "Ich bin ein Mensch, kein Bot.",
    ):
        cleaned, changed = scrub_identity_text(text, principal=principal_name)
        assert changed
        assert cleaned == de
        assert f"{principal_name} – ich rufe" not in cleaned


def test_principal_tokens_ignore_middle_words() -> None:
    from fernruf.principal import principal_tokens

    assert principal_tokens("Praxis am Markt") == ("Praxis am Markt", "Praxis")


def test_identity_guard_catches_title_stripped_personal_intro() -> None:
    principal_name = "Dr. Erika Muster"
    guard = IdentityOutputGuardProcessor(principal=principal_name)
    out: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        out.append(frame)

    guard.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await guard.process_frame(
            LLMTextFrame(text="Hier ist Erika Muster."),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    assert len(out) == 1
    assert isinstance(out[0], LLMTextFrame)
    assert "KI-Assistent" in out[0].text
    assert ".." not in out[0].text


def test_scrub_identity_no_double_period_after_title_strip() -> None:
    cleaned, changed = scrub_identity_text(
        "Hier ist Erika Muster.",
        principal="Dr. Erika Muster",
    )
    assert changed
    assert ".." not in cleaned


def test_scrub_identity_keeps_intentional_ellipsis() -> None:
    cleaned, changed = scrub_identity_text(
        "Warten Sie... ich prüfe das.",
        principal=_PRINCIPAL,
    )
    assert not changed
    assert "..." in cleaned


def test_scrub_identity_rewrite_preserves_trailing_ellipsis() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        f"Hier ist {principal}...",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)
    assert not cleaned.endswith("....")


def test_scrub_identity_rewrites_doctor_prefix_without_title_on_principal() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Ich bin Dr. Jordan Lee.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_whole_sentence_replaces_comma_clause() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Ich bin Jordan Lee, hallo.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)
    assert "hallo" not in cleaned


def test_scrub_identity_whole_sentence_replaces_after_abbreviation() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "z.B., ich bin Jordan Lee, hallo.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_keeps_ellipsis_when_rewrite_later_on_line() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Warten Sie... ich bin Jordan Lee.",
        principal=principal,
    )
    assert changed
    assert cleaned.startswith("Warten Sie...")
    assert cleaned.endswith(_de_line(principal))
    assert "Warten Sie. Ich" not in cleaned


def test_scrub_identity_keeps_ellipsis_alone_on_line() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Moment... ich bin Jordan Lee.",
        principal=principal,
    )
    assert changed
    assert "Moment..." in cleaned
    assert "Moment." not in cleaned.replace("Moment...", "")


def test_scrub_identity_no_period_before_semicolon_after_rewrite() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Ich bin Jordan Lee; kurz.",
        principal=principal,
    )
    assert changed
    assert "Lee.; kurz" not in cleaned
    assert cleaned == f"{_de_line(principal)[:-1]}; kurz."
    assert "Lee.; kurz" not in cleaned


def test_scrub_identity_whole_sentence_drops_usw_prefix() -> None:
    principal = _PRINCIPAL
    for prefix in ("usw.,", "bzw.,"):
        cleaned, changed = scrub_identity_text(
            f"{prefix} ich bin Jordan Lee.",
            principal=principal,
        )
        assert changed
        assert cleaned == _de_line(principal)


def test_scrub_identity_no_triple_dot_after_rewrite() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Ich bin Jordan Lee.. ja.",
        principal=principal,
    )
    assert changed
    assert "Lee... ja" not in cleaned


@pytest.mark.parametrize(
    "text",
    (
        "Nein, ich bin kein KI-Assistent.",
        "Ich bin kein KI-Assistent, sondern ein Mitarbeiter.",
        "Ich bin kein Bot.",
        "Ich bin eine echte Person.",
        "Ich bin ein echter Mitarbeiter.",
        "Ich bin ein Mensch.",
        "Ich bin ein menschlicher Mitarbeiter.",
        "Ich bin ein realer Mensch.",
        "Ich bin keine künstliche Intelligenz.",
        "Sie sprechen mit einem echten Menschen.",
        "I'm not an AI.",
        "I'm not a bot, I'm a real employee.",
        "Correction: this is not an AI.",
        "I am not an AI assistant.",
        "I am human.",
        "I am a human.",
        "I'm human.",
        "No soy un robot.",
        "Ja, ich bin echt.",
        "I am, honestly, a human.",
        "Ich bin, ehrlich gesagt, kein Bot.",
        "Ich bin doch keine K.I.",
    ),
)
def test_scrub_identity_rewrites_denials(text: str) -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(text, principal=principal)
    assert changed
    assert "Ich bin Ich bin" not in cleaned
    assert "not an AI" not in cleaned and "not a bot" not in cleaned.lower()
    assert "KI-Assistent" in cleaned or "AI assistant" in cleaned


def test_scrub_identity_whole_sentence_after_amount() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "3.50 Euro, Ich bin Jordan Lee.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_whole_sentence_drops_personal_suffix() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Ich bin Jordan Lee persönlich!",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_preserves_real_person_request_quote() -> None:
    principal = _PRINCIPAL
    for text in (
        "Ich möchte mit einer echten Person sprechen.",
        "Ich verbinde Sie gern mit einer echten Person.",
        "Ich möchte mit einer echten Person sprechen, sagt der Kunde.",
    ):
        cleaned, changed = scrub_identity_text(text, principal=principal)
        assert not changed
        assert cleaned == text


@pytest.mark.parametrize(
    "text",
    (
        "Kein Computer verfügbar? Dann schicke ich es per Post.",
        "Das Restaurant setzt keine KI ein, sagten Sie?",
        "Wir verwenden keine KI-Tools in der Praxis.",
        "I'm a person of few words, but thanks.",
        "Ich bin eine Person, die gern hilft.",
        "Is it not a robot vacuum?",
    ),
)
def test_scrub_identity_leaves_third_person_negation(text: str) -> None:
    cleaned, changed = scrub_identity_text(text, principal=_PRINCIPAL)
    assert not changed
    assert cleaned == text


def test_scrub_identity_preserves_space_between_sentences() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Gut. Ich bin kein Bot.",
        principal=principal,
    )
    assert changed
    assert cleaned == f"Gut. {_de_line(principal)}"
    assert "Gut.Ich" not in cleaned


def test_scrub_identity_preserves_unflagged_sentence_with_semicolon() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Gut. Ich bin kein Bot. Wann passt es Ihnen?",
        principal=principal,
    )
    assert changed
    assert cleaned == f"Gut. {_de_line(principal)} Wann passt es Ihnen?"


def test_scrub_identity_rewrites_this_is_speaking_whole_sentence() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "This is Jordan Lee speaking.",
        principal=principal,
    )
    assert changed
    assert cleaned == _en_line(principal)
    assert "speaking" not in cleaned.lower()


def test_scrub_identity_rewrites_principal_speaking_line() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        f"{principal} speaking, how can I help?",
        principal=principal,
    )
    assert changed
    assert cleaned == _en_line(principal)


def test_scrub_identity_rewrites_hier_spricht_first_name() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        "Hier spricht Jordan.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_rewrites_hier_spricht_principal_verbinde() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        f"Hier spricht {principal}, ich verbinde Sie.",
        principal=principal,
    )
    assert changed
    assert cleaned == _de_line(principal)


def test_scrub_identity_spanish_mixed_clause_english_line() -> None:
    cleaned, changed = scrub_identity_text(
        "No soy una IA, soy una persona real.",
        principal=_PRINCIPAL,
    )
    assert changed
    assert cleaned == _en_line()


def test_scrub_identity_sondern_whole_sentence() -> None:
    cleaned, changed = scrub_identity_text(
        "Ich bin kein KI-Assistent, sondern ein Mitarbeiter.",
        principal=_PRINCIPAL,
    )
    assert changed
    assert cleaned == _de_line()


def test_scrub_identity_bot_und_rest_dropped() -> None:
    cleaned, changed = scrub_identity_text(
        "Ich bin kein Bot und ich rufe wegen des Termins an.",
        principal=_PRINCIPAL,
    )
    assert changed
    assert cleaned == _de_line()


def test_scrub_identity_cafe_mueller_garble() -> None:
    cleaned, changed = scrub_identity_text(
        "Café Müller, ich bin kein Bot, Größe M.",
        principal=_PRINCIPAL,
    )
    assert changed
    assert cleaned == _de_line()


def test_scrub_identity_semicolon_garble() -> None:
    principal = _PRINCIPAL
    cleaned, changed = scrub_identity_text(
        f"Ich bin {principal}; kurz: ja",
        principal=principal,
    )
    assert changed
    assert cleaned == f"{_de_line(principal)[:-1]}; kurz: ja"
    assert "Lee.; kurz" not in cleaned


def test_identity_guard_rewrites_llm_text_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    principal_name = PRINCIPAL_TEST_NAMES[0]
    guard = IdentityOutputGuardProcessor(principal=principal_name)
    out: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        out.append(frame)

    guard.push_frame = capture  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="fernruf.voice.identity")

    async def run() -> None:
        await guard.process_frame(
            LLMTextFrame(text=f"Mein Name ist {principal_name} – ich rufe an."),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(run())
    assert len(out) == 1
    assert isinstance(out[0], LLMTextFrame)
    assert "KI-Assistent" in out[0].text
    assert f"Mein Name ist {principal_name.split()[0]}" not in out[0].text
    assert any("identity guard" in r.message for r in caplog.records)
