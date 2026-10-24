from __future__ import annotations

import pytest

from fernruf.llm.tools import CALL_TOOLS
from fernruf.principal import format_phrase_templates
from fernruf.settings import Settings
from fernruf.voice.prompt import build_system_prompt
from tests.conftest import PRINCIPAL_TEST_NAMES


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_vague_test_task_prompt_audio_check_and_no_invented_surveys(
    principal_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", principal_name)
    prompt = build_system_prompt(
        Settings(_env_file=None, principal_name=principal_name),
        "Kurzer Testanruf",
    )
    lower = prompt.lower()
    assert "ki-assistent" in lower
    assert principal_name.lower() in lower
    assert f"nicht {principal_name.lower()}" in lower
    assert "kurzer testanruf" in lower
    assert "erfinde keine" in lower
    assert "umfragen" in lower
    assert "testanruf" in lower
    assert "verständlich" in lower or "verstaendlich" in lower
    assert "end_call" in lower
    assert "durchwahl" in lower and "keine fragen" in lower
    assert "melde mich später" in lower
    assert "nicht ausführst" in lower
    assert f"{principal_name.lower()}, nicht du" in lower
    assert "beende das gespräch" in lower
    assert "keine erledigung" in lower
    assert "nachdem die gegenseite geantwortet" in lower
    assert "wollte fragen, ob" in lower
    assert "nur für vage aufgaben" in lower


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_goodbye_confirms_a_concrete_answer_in_one_short_clause(
    principal_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", principal_name)
    settings = Settings(_env_file=None, principal_name=principal_name)
    prompt = build_system_prompt(settings, "Frage, wann sie Zeit für ein Telefonat hat.")
    lower = prompt.lower()
    farewell = format_phrase_templates("de", principal_name)["farewell_example"].lower()
    assert farewell in lower
    assert "konkrete antwort" in lower
    assert "uhrzeit" in lower
    assert "ja/nein" in lower
    assert "zahl oder name" in lower
    assert "nicht „dann rufe ich an“" in lower
    assert "i'll call you back" in lower
    assert "kein allgemeiner wunsch" in lower
    assert "halte ``goodbye`` kurz" in lower
    end_call = next(t for t in CALL_TOOLS if t["function"]["name"] == "end_call")
    desc = end_call["function"]["description"].lower()
    assert "one short clause" in desc
    assert "thank you, then the principal will get in touch at 7 p.m. goodbye." in desc
    assert "call language" in desc
    assert "calling back" in desc
    assert "well-wish" in desc


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_farewell_example_follows_the_call_language(
    principal_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", principal_name)
    en_farewell = format_phrase_templates("en", principal_name)["farewell_example"]
    de_farewell = format_phrase_templates("de", principal_name)["farewell_example"]
    english = build_system_prompt(
        Settings(_env_file=None, principal_name=principal_name, call_language="en"),
        "Ask whether they have time tonight and at what time.",
    )
    assert en_farewell in english
    assert de_farewell not in english
    assert "English" in english
    lower = english.lower()
    assert "what is it about?" in lower
    assert "sprecherrolle" in lower
    assert f"{principal_name.lower()}, nicht du" in lower

    chinese = build_system_prompt(
        Settings(_env_file=None, principal_name=principal_name, call_language="zh"),
        "请问对方今晚是否有时间和几点方便。",
    )
    assert principal_name in chinese
    assert "谢谢" in chinese and "再见" in chinese
    assert de_farewell not in chinese
    assert "Goodbye." not in chinese

    french_line = f"Merci, {principal_name} vous contactera à 16 heures. Au revoir."
    french = build_system_prompt(
        Settings(
            _env_file=None,
            principal_name=principal_name,
            call_language="fr",
            call_phrases={"farewell_example": french_line, "goodbye": "Au revoir."},
        ),
        "Demandez si mardi convient.",
    )
    assert french_line in french
    assert de_farewell not in french
    assert "Französisch" in french


def test_concrete_task_still_forbids_off_task_questions() -> None:
    prompt = build_system_prompt(
        Settings(_env_file=None),
        "Frage Herrn Müller, ob der Termin am Dienstag passt.",
    )
    lower = prompt.lower()
    assert "erfinde keine" in lower
    assert "dienstag" in lower
    assert "nicht zur aufgabe" in lower


def test_first_turn_must_state_purpose_and_must_say() -> None:
    prompt = build_system_prompt(
        Settings(_env_file=None, call_language="en"),
        "Ziel: ask the callee how their day has been, then end warmly.",
    )
    lower = prompt.lower()
    assert "erster eigener redebeitrag" in lower
    assert "muss sagen" in lower
    assert "guten abend" in lower
    assert "vielen dank" in lower
    assert "auflegen noch eine ablehnung" in lower
    end_call = next(t for t in CALL_TOOLS if t["function"]["name"] == "end_call")
    desc = end_call["function"]["description"].lower()
    assert "must_say" in desc
    assert "vielen dank" in desc
