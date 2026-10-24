"""Call language: Jev detection, fixed lines, Whisper, Fish cache, simulate."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from fernruf.call_record import get_call
from fernruf.cli import main
from fernruf.call_settings.store import update_call_settings
from fernruf.language import (
    LANGUAGE_CRITERIA,
    LANGUAGE_INSTRUCTIONS,
    PHRASE_KEYS,
    build_language_request,
    detect_call_language,
    detect_call_language_local,
    prepare_call_settings,
    resolve_phrases,
    task_detection_text,
)
from fernruf.principal import format_phrase_templates
from tests.conftest import DEFAULT_TEST_PRINCIPAL, PRINCIPAL_TEST_NAMES
from fernruf.service.manager import format_task
from fernruf.mood import JEV_URL, JevMoodClient, MoodReading, build_mood_request, reset_mood_notices
from fernruf.settings import Settings
from fernruf.greeting import BERLIN, part_of_day, time_greeting
from fernruf.stt.whisper import _transcribe_kwargs
from fernruf.tts.fish_audio_cache import fish_cache_key
from fernruf.voice.prompt import build_system_prompt
from fernruf.voice.simulate import SimulateConversation

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_jev() -> None:
    reset_mood_notices()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "typesafe_enabled": True,
        "typesafe_api_key": "test-token-value",
        "language_model": "jev-latest",
        "language_timeout_ms": 2000,
        "language_min_confidence": 0.6,
        "call_settings_db": str(tmp_path / "fernruf.sqlite"),
        "llm_model": "vendor/example-model",
        "llm_mock": True,
        "voice_mock": True,
        "fish_voice_id": "voice-default",
        "whisper_language": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _answer(choice: str, confidence: float = 0.95) -> httpx.Response:
    return httpx.Response(
        200,
        json={"answers": {"language": {"choice": choice, "confidence": confidence}}},
    )


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
def test_phrase_tables_cover_de_en_zh(principal_name: str) -> None:
    for code in ("de", "en", "zh"):
        phrases = format_phrase_templates(code, principal_name)
        assert set(phrases) == set(PHRASE_KEYS)
        for key in PHRASE_KEYS:
            assert phrases[key].strip()
        assert principal_name in phrases["ai_notice"]
    de = format_phrase_templates("de", principal_name)
    en = format_phrase_templates("en", principal_name)
    zh = format_phrase_templates("zh", principal_name)
    assert "KI-Assistent" in de["ai_notice"]
    assert "Auftrag" in de["ai_notice"]
    assert "AI assistant" in en["ai_notice"]
    assert "behalf" in en["ai_notice"]
    assert "人工智能" in zh["ai_notice"]
    assert "代表" in zh["ai_notice"]
    assert de["farewell_example"] == (
        f"Vielen Dank, dann meldet sich {principal_name} um 16 Uhr. Auf Wiederhören."
    )
    assert en["farewell_example"] == (
        f"Thank you, then {principal_name} will get in touch at 7 p.m. Goodbye."
    )
    assert zh["farewell_example"].endswith("再见。")
    assert principal_name in zh["farewell_example"]


@pytest.mark.parametrize("principal_name", PRINCIPAL_TEST_NAMES)
@pytest.mark.parametrize("lang", ("de", "en", "zh"))
def test_resolve_phrases_uses_configured_principal(
    tmp_path: Path, lang: str, principal_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", principal_name)
    settings = _settings(tmp_path, principal_name=principal_name)

    phrases, spoken, source = asyncio.run(resolve_phrases(settings, lang))

    assert spoken == lang and source == "table"
    assert principal_name in phrases["ai_notice"]


def test_translated_phrases_must_name_principal_or_fallback_en(tmp_path: Path) -> None:
    settings = _settings(tmp_path, principal_name="Erika Muster")

    phrases, spoken, source = asyncio.run(resolve_phrases(settings, "fr"))
    assert spoken == "en" and source == "fixed_en"
    assert "Erika Muster" in phrases["ai_notice"]


def test_detect_de_en_zh_and_explicit_instruction(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        assert request.url == httpx.URL(JEV_URL)
        assert request.headers["Authorization"] == "Bearer test-token-value"
        state = str(body["state"])
        if "Englisch" in state or "Jordan" in state:
            choice = "en"
        elif "星期二" in state:
            choice = "zh"
        else:
            choice = "de"
        return _answer(choice, 0.97)

    settings = _settings(tmp_path, llm_model="example/model-b")

    async def _run(task: str):
        return await detect_call_language(
            settings, task, transport=httpx.MockTransport(handler)
        )

    german = asyncio.run(_run("Frag Herrn Müller, ob der Termin am Dienstag noch passt."))
    english = asyncio.run(_run("Please ask Jordan if Tuesday still works for the handover."))
    chinese = asyncio.run(_run("请问对方星期二的交接时间是否仍然合适，并记下答复。"))
    explicit = asyncio.run(_run("Bitte ruf auf Englisch an und frag, ob der Termin passt."))

    assert german.language == "de" and german.source == "jev"
    assert english.language == "en" and english.confidence == 0.97
    assert chinese.language == "zh"
    assert explicit.language == "en"
    body = seen[-1]
    assert body["model"] == "jev-latest"
    assert ("gr" + "ok") not in json.dumps(body).lower()
    assert body["questions"]["language"]["instructions"] == LANGUAGE_INSTRUCTIONS
    assert set(body["questions"]["language"]["criteria"]) == set(LANGUAGE_CRITERIA)
    assert "ruf auf Englisch" in str(body["state"])


def test_short_text_low_confidence_and_missing_key(tmp_path: Path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Jev should not be called for {request.content!r}")

    short = asyncio.run(
        detect_call_language(
            _settings(tmp_path),
            "Hi",
            transport=httpx.MockTransport(explode),
        )
    )
    assert short.language == "de" and short.reason == "short_text"

    low = asyncio.run(
        detect_call_language(
            _settings(tmp_path),
            "Please ask if Tuesday still works for everyone.",
            transport=httpx.MockTransport(lambda _request: _answer("en", 0.2)),
        )
    )
    assert low.language == "de" and low.reason == "low_confidence"

    missing = asyncio.run(
        detect_call_language(
            _settings(tmp_path, typesafe_api_key="", typesafe_enabled=True),
            "Please ask if Tuesday still works for everyone.",
            transport=httpx.MockTransport(explode),
        )
    )
    assert missing.language == "en" and missing.source == "local"


def test_timeout_and_rejected_key_fall_back_to_german(tmp_path: Path) -> None:
    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return _answer("en")

    timed = asyncio.run(
        detect_call_language(
            _settings(tmp_path, language_timeout_ms=50),
            "Please ask if Tuesday still works for everyone.",
            transport=httpx.MockTransport(slow),
        )
    )
    assert timed.language == "de" and timed.reason == "timeout"

    hits = {"n": 0}

    def rejected(_request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(401, json={"error": "nope"})

    settings = _settings(tmp_path)

    async def _both() -> tuple[object, MoodReading]:
        detected = await detect_call_language(
            settings,
            "Please ask if Tuesday still works for everyone.",
            transport=httpx.MockTransport(rejected),
        )
        mood = await JevMoodClient(settings, transport=httpx.MockTransport(rejected)).detect(
            "Hello there."
        )
        return detected, mood

    detected, mood = asyncio.run(_both())
    assert detected.language == "de" and detected.reason == "auth"
    assert mood.choice == "neutral" and mood.error == "auth"
    assert hits["n"] == 1


def test_prompt_names_the_call_language(tmp_path: Path) -> None:
    settings = asyncio.run(
        prepare_call_settings(
            _settings(tmp_path),
            "Please ask if Tuesday still works.",
            transport=httpx.MockTransport(lambda _request: _answer("en")),
        )
    )
    prompt = build_system_prompt(settings, "Please ask if Tuesday still works.")
    assert "English" in prompt
    assert "AI assistant" in prompt
    assert DEFAULT_TEST_PRINCIPAL in prompt
    assert "May I record this call" in prompt
    assert "genau einmal erneut" in prompt
    evening = datetime(2026, 10, 26, 20, 34, tzinfo=BERLIN)
    evening_prompt = build_system_prompt(settings, "Please ask if Tuesday still works.", now=evening)
    assert "Europe/Berlin: 20:34" in evening_prompt
    assert "Good evening" in evening_prompt
    assert "Hello" in evening_prompt
    assert "Good afternoon" not in evening_prompt
    german = build_system_prompt(Settings(_env_file=None), "Kurzer Testanruf")
    assert "Deutsch" in german
    assert "auf Deutsch" in german


def test_whisper_follows_detection_even_when_env_is_german(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The live kwargs use the detected code. FERNRUF_WHISPER_LANGUAGE does not override it."""
    caplog.set_level("INFO")
    prepared = asyncio.run(
        prepare_call_settings(
            _settings(tmp_path, whisper_language="de"),
            "Please ask if Tuesday still works for the handover.",
            transport=httpx.MockTransport(lambda _request: _answer("en")),
        )
    )
    kw = _transcribe_kwargs(prepared)
    assert prepared.call_language == "en"
    assert prepared.whisper_language == "en"
    assert kw["language"] == "en"
    assert kw["initial_prompt"] == "Phone call in English."
    assert any(
        "call start detected_language=en whisper_language=en" in r.message
        and "Phone call in English." in r.message
        for r in caplog.records
    )
    from fernruf.voice.services.faster_whisper_stt import FasterWhisperSTTService

    caplog.clear()
    FasterWhisperSTTService(call_settings=prepared)
    live = [r.message for r in caplog.records if "STT transcribe kwargs (live path)" in r.message]
    assert live
    assert "'language': 'en'" in live[0]
    assert "Phone call in English." in live[0]


def test_berlin_evening_greeting_is_not_afternoon() -> None:
    evening = datetime(2026, 10, 26, 20, 34, tzinfo=BERLIN)
    afternoon = datetime(2026, 10, 26, 15, 0, tzinfo=BERLIN)
    morning = datetime(2026, 10, 26, 9, 0, tzinfo=BERLIN)
    assert part_of_day(evening) == "evening"
    assert part_of_day(datetime(2026, 10, 26, 18, 0, tzinfo=BERLIN)) == "evening"
    assert part_of_day(datetime(2026, 10, 26, 17, 59, tzinfo=BERLIN)) == "afternoon"
    assert time_greeting("en", evening) == ("Good evening", "Hello")
    assert time_greeting("de", evening) == ("Guten Abend", "Hallo")
    assert time_greeting("en", afternoon)[0] == "Good afternoon"
    assert time_greeting("zh", morning) == ("早上好", "您好")


def test_whisper_env_is_the_fallback_when_detection_fails(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")

    def explode(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Jev should not be called")

    prepared = asyncio.run(
        prepare_call_settings(
            _settings(tmp_path, whisper_language="en"),
            "Hi",
            transport=httpx.MockTransport(explode),
        )
    )
    kw = _transcribe_kwargs(prepared)
    assert prepared.call_language == "en"
    assert kw["language"] == "en"
    assert kw["initial_prompt"] == format_phrase_templates("en", DEFAULT_TEST_PRINCIPAL)[
        "whisper_initial_prompt"
    ]
    assert any("detected_language=fallback:short_text" in r.message for r in caplog.records)
    assert any("whisper_language=en" in r.message for r in caplog.records)


def test_fish_cache_key_includes_language() -> None:
    text = "One moment…"
    german = fish_cache_key(text, voice_id="v", model="s2.1-pro-free", latency="balanced", language="de")
    english = fish_cache_key(text, voice_id="v", model="s2.1-pro-free", latency="balanced", language="en")
    assert german != english


def test_per_language_fish_voice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_FISH_VOICE_ID_EN", "voice-en")
    prepared = asyncio.run(
        prepare_call_settings(
            _settings(tmp_path, fish_voice_id="voice-de"),
            "Please ask if Tuesday still works for the handover.",
            transport=httpx.MockTransport(lambda _request: _answer("en")),
        )
    )
    assert prepared.fish_voice_id == "voice-en"
    assert prepared.filler_phrase == format_phrase_templates("en", DEFAULT_TEST_PRINCIPAL)["filler"]


def test_german_task_with_accented_names_stays_de(tmp_path: Path) -> None:
    settings = _settings(tmp_path, typesafe_enabled=False)
    detection = detect_call_language_local(
        settings,
        "Ruf im Café Müller an und reserviere einen Tisch",
    )
    assert detection.language == "de"
    assert detection.reason in ("heuristic_de", "default_language")


def test_french_stopwords_use_fixed_english_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path, typesafe_enabled=False)
    detection = detect_call_language_local(
        settings,
        "Bonjour, merci de confirmer le rendez-vous demain matin.",
    )
    assert detection.language == "es"
    assert detection.reason == "heuristic_non_de"
    phrases, spoken, source = asyncio.run(resolve_phrases(settings, detection.language))
    assert spoken == "en" and source == "fixed_en"


def test_other_language_uses_fixed_english_without_llm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    principal = settings.require_principal()

    first = asyncio.run(resolve_phrases(settings, "fr"))
    second = asyncio.run(resolve_phrases(settings, "fr"))
    assert first[1] == "en" and first[2] == "fixed_en"
    assert second[2] == "fixed_en"
    assert principal in first[0]["ai_notice"]
    assert first[0]["ai_notice"] == format_phrase_templates("en", principal)["ai_notice"]

    fallen = asyncio.run(resolve_phrases(_settings(tmp_path), "es"))
    assert fallen[1] == "en" and fallen[2] == "fixed_en"
    assert fallen[0]["ai_notice"] == format_phrase_templates("en", DEFAULT_TEST_PRINCIPAL)[
        "ai_notice"
    ]


def test_language_module_does_not_hardcode_a_chat_model() -> None:
    source = (ROOT / "fernruf" / "language.py").read_text(encoding="utf-8").lower()
    for needle in ("gpt-4", "claude"):
        assert needle not in source
    body = build_language_request("Bitte auf Englisch anrufen.", model="jev-latest")
    assert body["model"] == "jev-latest"
    assert body["questions"]["language"]["type"] == "choice"


def test_mood_request_names_a_non_german_call(tmp_path: Path) -> None:
    body = build_mood_request(
        "Tuesday is fine.",
        "Does Tuesday work?",
        model="jev-latest",
        call_language="en",
    )
    assert "English" in body["questions"]["mood"]["instructions"]
    assert "English" in body["state"]
    assert "Tuesday is fine." in body["state"]
    assert set(body["questions"]["mood"]["criteria"]) == {
        "freundlich",
        "neutral",
        "genervt",
        "traurig",
        "belustigt",
        "misstrauisch",
        "gestresst",
    }
    german = build_mood_request("Dienstag passt.", None, model="jev-latest")
    assert "Gesprächssprache" not in german["state"]

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"answers": {"mood": {"choice": "freundlich", "confidence": 1}}},
        )

    settings = _settings(tmp_path, call_language="en", llm_model="example/model-b")

    async def _mood() -> MoodReading:
        return await JevMoodClient(settings, transport=httpx.MockTransport(handler)).detect(
            "Tuesday is fine.",
            prior_bot="Does Tuesday work?",
        )

    reading = asyncio.run(_mood())
    assert reading.choice == "freundlich"
    encoded = json.dumps(seen["body"]).lower()
    assert "english" in encoded
    assert ("gr" + "ok") not in encoded


def test_simulate_english_task_speaks_english_notice(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        latency_log_path=str(tmp_path / "latency.jsonl"),
    )

    async def _run():
        prepared = await prepare_call_settings(
            settings,
            "Please ask if Tuesday still works for the handover.",
            transport=httpx.MockTransport(lambda _request: _answer("en", 0.98)),
        )
        result = await SimulateConversation(
            prepared,
            "Please ask if Tuesday still works for the handover.",
        ).run()
        return result

    result = asyncio.run(_run())
    assert result.call_language == "en"
    assert result.transcript[0].speaker == "agent"
    assert "AI assistant" in result.transcript[0].text
    assert DEFAULT_TEST_PRINCIPAL in result.transcript[0].text
    record = get_call(result.call_id, settings=settings)
    assert record is not None
    assert record["call_language"] == "en"


def test_m2_test_call_simulate_english_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "fake-ari-password-16bytes")
    monkeypatch.setenv("FERNRUF_ALLOWLIST", "+493023125123")
    monkeypatch.setenv("FERNRUF_QUIET_HOURS", "off")
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", DEFAULT_TEST_PRINCIPAL)
    monkeypatch.setenv("FERNRUF_LLM_MOCK", "true")
    monkeypatch.setenv("FERNRUF_VOICE_MOCK", "true")
    monkeypatch.setenv("FERNRUF_SETTINGS_DB", str(tmp_path / "fernruf.sqlite"))
    monkeypatch.setenv("FERNRUF_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("FERNRUF_LATENCY_LOG", str(tmp_path / "latency.jsonl"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-token-value")
    monkeypatch.setenv("FERNRUF_WHISPER_LANGUAGE", "")

    def handler(_request: httpx.Request) -> httpx.Response:
        return _answer("en", 0.96)

    real_post = __import__("fernruf.language", fromlist=["post_systemone"]).post_systemone

    async def routed(**kwargs: object):
        kwargs["transport"] = httpx.MockTransport(handler)
        return await real_post(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("fernruf.language.post_systemone", routed)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "m2-test-call",
            "--simulate",
            "--to",
            "+493023125123",
            "--task",
            "Please ask if Tuesday still works for the handover.",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.find("{") :])
    assert payload["call_language"] == "en"
    assert "AI assistant" in payload["first_line"]
    assert DEFAULT_TEST_PRINCIPAL in payload["first_line"]
    agent_lines = [line["text"] for line in payload["lines"] if line["speaker"] == "agent"]
    assert any("Thanks for your time" in line for line in agent_lines)
    assert any("Goodbye" in line for line in agent_lines)
    record = get_call(
        payload["call_id"],
        settings=Settings(_env_file=None, call_settings_db=str(tmp_path / "fernruf.sqlite")),
    )
    assert record is not None
    assert record["call_language"] == "en"


VROOM_TASK = {
    "goal": (
        "Tell Alex that the ExampleApp test release v1.0.0 is ready, "
        "then end the call."
    ),
    "context": (
        "Alex asked to be called when the release was ready. "
        "The download link is already in his their team chat. "
        "It installs over v0.4.0 without uninstalling."
    ),
    "must_say": [
        "The ExampleApp test release v1.0.0",
        "The download link is in your chat with Example Bot.",
    ],
    "must_not": [
        "Do not agree to anything or make commitments.",
        "Do not read out hashes or links.",
    ],
    "decision_limits": "None. This is a notification only.",
    "fallback": (
        "If voicemail, leave a one-sentence message that the ExampleApp test release v1.0.0"
        "is ready and the link is in chat, then hang up."
    ),
}


def test_explicit_language_skips_detection(tmp_path: Path) -> None:
    """language=en wins even when Jev would classify the wrapped brief as German."""
    hits = {"n": 0}

    def would_say_german(_request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return _answer("de")

    settings = _settings(tmp_path)
    update_call_settings({"default_language": "de"}, settings=settings)
    sample = task_detection_text(VROOM_TASK)
    wrapped = format_task(VROOM_TASK, persona=None, language="en", dtmf_prefix=None)
    prepared = asyncio.run(
        prepare_call_settings(
            settings,
            wrapped,
            language="en",
            detection_text=sample,
            transport=httpx.MockTransport(would_say_german),
        )
    )
    assert hits["n"] == 0
    assert prepared.call_language == "en"
    assert prepared.disclosure_prefix == format_phrase_templates("en", DEFAULT_TEST_PRINCIPAL)[
        "ai_notice"
    ]
    assert "Ziel:" in wrapped
    assert "Muss sagen" not in sample


def test_detection_reads_task_text_not_default_language(tmp_path: Path) -> None:
    """Without language, Jev sees goal, context, and must_say. Not the German brief or default_language."""
    settings = _settings(tmp_path)
    update_call_settings({"default_language": "fr"}, settings=settings)
    sample = task_detection_text(VROOM_TASK)
    wrapped = format_task(VROOM_TASK, persona=None, language=None, dtmf_prefix=None)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        state = json.loads(request.content.decode())["state"]
        seen["state"] = state
        return _answer("en")

    prepared = asyncio.run(
        prepare_call_settings(
            settings,
            wrapped,
            detection_text=sample,
            transport=httpx.MockTransport(handler),
        )
    )
    state = seen["state"]
    assert prepared.call_language == "en"
    assert state == " ".join(sample.split())
    assert "is ready" in state
    assert "The ExampleApp test release v1.0.0" in state
    assert "Example Bot" in state
    assert "Muss sagen" not in state
    assert "Ziel:" not in state
    assert "voicemail" not in state
    assert "Do not agree" not in state
    assert "fr" not in state

    other = _settings(tmp_path, call_settings_db=str(tmp_path / "other.sqlite"))
    update_call_settings({"default_language": "fr"}, settings=other)

    def explode(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Jev should not be called for a short text")

    short = asyncio.run(
        prepare_call_settings(other, "Hi", transport=httpx.MockTransport(explode))
    )
    assert short.call_language == "de"


def test_english_call_ignores_saved_german_opening_line(tmp_path: Path) -> None:
    """language=en speaks the English disclosure, not the saved German opening_line."""
    settings = _settings(tmp_path)
    german = format_phrase_templates("de", DEFAULT_TEST_PRINCIPAL)["ai_notice"]
    update_call_settings(
        {"opening_line": german, "default_language": "de"},
        settings=settings,
    )

    def explode(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Jev should not be called when language is set")

    english = asyncio.run(
        prepare_call_settings(
            settings,
            "Ziel: anything",
            language="en",
            transport=httpx.MockTransport(explode),
        )
    )
    assert english.disclosure_prefix == format_phrase_templates("en", DEFAULT_TEST_PRINCIPAL)[
        "ai_notice"
    ]
    assert "KI-Assistent" not in english.disclosure_prefix
    assert english.call_language == "en"

    custom = f"Hallo, {DEFAULT_TEST_PRINCIPAL} Testzusatz, hier ist der KI-Hinweis."
    german_settings = _settings(tmp_path, call_settings_db=str(tmp_path / "de.sqlite"))
    update_call_settings(
        {"opening_line": custom, "default_language": "de"},
        settings=german_settings,
    )
    matched = asyncio.run(
        prepare_call_settings(
            german_settings,
            "Ziel: Kurzer deutscher Hinweis.",
            language="de",
            transport=httpx.MockTransport(explode),
        )
    )
    assert matched.disclosure_prefix == format_phrase_templates("de", DEFAULT_TEST_PRINCIPAL)[
        "ai_notice"
    ]
    assert custom not in matched.disclosure_prefix
