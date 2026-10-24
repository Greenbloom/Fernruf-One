"""Call language: explicit ``language`` wins, else local heuristics / default_language.

Optional TypeSafe Jev runs only when ``FERNRUF_TYPESAFE_ENABLED=true`` and
``TYPESAFE_API_KEY`` is set. Fixed lines for German, English, and Chinese are
hand-written. Any other detected language uses the fixed English phrase table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from fernruf.mood import (
    jev_auth_is_blocked,
    mark_jev_auth_failed,
    parse_jev_choice,
    post_systemone,
)
from fernruf.principal import PHRASE_TEMPLATE_KEYS, format_phrase_templates, require_principal_name
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.language")

PHRASE_KEYS: tuple[str, ...] = (
    "ai_notice",
    "consent_question",
    "filler",
    "goodbye",
    "llm_failure_apology",
    "llm_failure_goodbye",
    "whisper_initial_prompt",
    "delay_apology",
    "identity_line",
    "privacy_notice",
    "reask",
    "farewell_example",
)

SHORT_TEXT_CHARS = 8

_EXPLICIT_LANG_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(auf|in)\s+Englisch\b", re.I), "en"),
    (re.compile(r"\bin\s+English\b", re.I), "en"),
    (re.compile(r"\b(auf|in)\s+Deutsch\b", re.I), "de"),
    (re.compile(r"\bin\s+German\b", re.I), "de"),
    (re.compile(r"\b(auf|in)\s+Chinesisch\b", re.I), "zh"),
    (re.compile(r"\bin\s+Chinese\b", re.I), "zh"),
    (re.compile(r"中文|汉语|普通话", re.I), "zh"),
)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_GERMAN_HINT = re.compile(
    r"(ä|ö|ü|ß|\b(und|der|die|das|nicht|bitte|danke|termin|anruf|frage)\b)",
    re.I,
)
_NON_GERMAN_STOP = re.compile(
    r"\b(le|la|les|je|vous|est|pas|une|des|pour|avec|el|los|soy|es|una|un|no\s+soy)\b",
    re.I,
)
_ENGLISH_HINT = re.compile(
    r"\b(the|and|please|thank|call|ask|tuesday|appointment|hello|would|could)\b",
    re.I,
)

LANGUAGE_INSTRUCTIONS = (
    "In welcher Sprache soll der Telefonanruf geführt werden? "
    "Entscheide nach der Sprache, in der die Aufgabe geschrieben ist, "
    "außer die Aufgabe nennt ausdrücklich eine andere Gesprächssprache."
)

LANGUAGE_CRITERIA: dict[str, str] = {
    "de": "Deutsch. Die Aufgabe ist auf Deutsch, und sie verlangt keine andere Gesprächssprache.",
    "en": "Englisch. Die Aufgabe ist auf Englisch, oder sie verlangt ausdrücklich Englisch.",
    "zh": "Chinesisch. Die Aufgabe ist auf Chinesisch, oder sie verlangt ausdrücklich Chinesisch.",
    "fr": "Französisch. Die Aufgabe ist auf Französisch, oder sie verlangt ausdrücklich Französisch.",
    "es": "Spanisch. Die Aufgabe ist auf Spanisch, oder sie verlangt ausdrücklich Spanisch.",
    "it": "Italienisch. Die Aufgabe ist auf Italienisch, oder sie verlangt ausdrücklich Italienisch.",
    "tr": "Türkisch. Die Aufgabe ist auf Türkisch, oder sie verlangt ausdrücklich Türkisch.",
    "pl": "Polnisch. Die Aufgabe ist auf Polnisch, oder sie verlangt ausdrücklich Polnisch.",
    "ru": "Russisch. Die Aufgabe ist auf Russisch, oder sie verlangt ausdrücklich Russisch.",
    "ar": "Arabisch. Die Aufgabe ist auf Arabisch, oder sie verlangt ausdrücklich Arabisch.",
    "ja": "Japanisch. Die Aufgabe ist auf Japanisch, oder sie verlangt ausdrücklich Japanisch.",
    "nl": "Niederländisch. Die Aufgabe ist auf Niederländisch, oder sie verlangt ausdrücklich Niederländisch.",
    "pt": "Portugiesisch. Die Aufgabe ist auf Portugiesisch, oder sie verlangt ausdrücklich Portugiesisch.",
    "uk": "Ukrainisch. Die Aufgabe ist auf Ukrainisch, oder sie verlangt ausdrücklich Ukrainisch.",
}

STATIC_LANGS = frozenset({"de", "en", "zh"})


@dataclass(frozen=True)
class LanguageDetection:
    language: str
    confidence: float
    source: str
    reason: str = ""
    latency_ms: float | None = None


def is_very_short(text: str) -> bool:
    compact = "".join((text or "").split())
    return len(compact) < SHORT_TEXT_CHARS


def normalize_language_code(raw: str) -> str:
    code = (raw or "").strip().lower().replace("_", "-")
    if code in LANGUAGE_CRITERIA:
        return code
    primary = code.split("-", 1)[0]
    if primary in LANGUAGE_CRITERIA:
        return primary
    return ""


def principal_for_phrases(settings: Settings) -> str:
    """Principal name for phrase templates (``FERNRUF_PRINCIPAL_NAME``)."""
    return settings.require_principal()


def static_phrases(code: str, *, principal: str) -> dict[str, str]:
    lang = normalize_language_code(code) or "de"
    if lang not in STATIC_LANGS:
        lang = "de"
    return format_phrase_templates(lang, require_principal_name(principal))


def phrases_for_settings(settings: Settings) -> dict[str, str]:
    """Fixed lines for this call. Unprepared settings stay on the German set."""
    lang = normalize_language_code(getattr(settings, "call_language", "") or "") or "de"
    principal = settings.require_principal()
    base = static_phrases(lang if lang in STATIC_LANGS else "de", principal=principal)
    stored = getattr(settings, "call_phrases", None) or {}
    if isinstance(stored, dict):
        for key in PHRASE_KEYS:
            val = stored.get(key)
            if isinstance(val, str) and val.strip():
                base[key] = val.strip()
    return base


def phrase(settings: Settings, key: str) -> str:
    return phrases_for_settings(settings)[key]


def task_detection_text(task: dict[str, Any] | str) -> str:
    """Text Jev should classify: goal, context, and must_say. Not the German brief."""
    if isinstance(task, str):
        return task.strip()
    parts: list[str] = []
    for key in ("goal", "context"):
        text = str(task.get(key) or "").strip()
        if text:
            parts.append(text)
    for item in task.get("must_say") or []:
        text = str(item).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def build_language_request(task: str, *, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "state": " ".join((task or "").split()),
        "questions": {
            "language": {
                "type": "choice",
                "instructions": LANGUAGE_INSTRUCTIONS,
                "criteria": dict(LANGUAGE_CRITERIA),
            }
        },
    }


def default_call_language(settings: Settings) -> str:
    """Saved ``default_language``, else ``FERNRUF_WHISPER_LANGUAGE``, else German."""
    try:
        from fernruf.call_settings.store import get_call_settings

        record = get_call_settings(settings)
        code = normalize_language_code(record.default_language or "")
        if code:
            return code
    except Exception:
        pass
    return fallback_language(settings)


def detect_call_language_local(settings: Settings, task: str) -> LanguageDetection:
    """Heuristic detection without network calls."""
    sample = (task or "").strip()
    if is_very_short(sample):
        return LanguageDetection(
            language=fallback_language(settings),
            confidence=1.0,
            source="local",
            reason="short_text",
        )
    for pattern, code in _EXPLICIT_LANG_HINTS:
        if pattern.search(sample):
            return LanguageDetection(
                language=code,
                confidence=0.95,
                source="local",
                reason="explicit_hint",
            )
    if _CJK.search(sample):
        return LanguageDetection(
            language="zh",
            confidence=0.85,
            source="local",
            reason="script_zh",
        )
    german = len(_GERMAN_HINT.findall(sample))
    english = len(_ENGLISH_HINT.findall(sample))
    if _NON_GERMAN_STOP.search(sample) and german < 2 and english < 2:
        return LanguageDetection(
            language="es",
            confidence=0.8,
            source="local",
            reason="heuristic_non_de",
        )
    if english > german and english >= 2:
        return LanguageDetection(
            language="en",
            confidence=0.75,
            source="local",
            reason="heuristic_en",
        )
    if german >= 2 and german > english:
        return LanguageDetection(
            language="de",
            confidence=0.75,
            source="local",
            reason="heuristic_de",
        )
    return LanguageDetection(
        language=default_call_language(settings),
        confidence=1.0,
        source="local",
        reason="default_language",
    )


def _fallback(reason: str, *, latency_ms: float | None = None, confidence: float = 0.0) -> LanguageDetection:
    return LanguageDetection(
        language="de",
        confidence=confidence,
        source="fallback",
        reason=reason,
        latency_ms=latency_ms,
    )


def _log_detection(detection: LanguageDetection, task: str) -> None:
    latency = "-" if detection.latency_ms is None else f"{detection.latency_ms:.0f}"
    LOG.info(
        "call language=%s source=%s confidence=%.2f reason=%s latency_ms=%s task_chars=%s",
        detection.language,
        detection.source,
        detection.confidence,
        detection.reason or "-",
        latency,
        len("".join((task or "").split())),
    )


async def detect_call_language(
    settings: Settings,
    task: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LanguageDetection:
    """Jev when TypeSafe is enabled; otherwise local heuristics / default_language."""
    if not settings.typesafe_requests_enabled():
        detection = detect_call_language_local(settings, task)
        _log_detection(detection, task)
        return detection
    if is_very_short(task):
        detection = _fallback("short_text")
        _log_detection(detection, task)
        return detection
    if jev_auth_is_blocked():
        detection = _fallback("auth")
        _log_detection(detection, task)
        return detection
    api_key = (settings.typesafe_api_key or "").strip()
    if not api_key:
        detection = detect_call_language_local(settings, task)
        _log_detection(detection, task)
        return detection
    timeout_s = max(settings.language_timeout_ms, 0) / 1000
    if timeout_s <= 0:
        detection = _fallback("timeout", latency_ms=0.0)
        _log_detection(detection, task)
        return detection
    body = build_language_request(task, model=settings.language_model)
    started = asyncio.get_running_loop().time()

    def elapsed() -> float:
        return (asyncio.get_running_loop().time() - started) * 1000

    try:
        response = await post_systemone(
            api_key=api_key,
            body=body,
            timeout_s=timeout_s,
            transport=transport,
        )
    except TimeoutError:
        detection = _fallback("timeout", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    except httpx.TimeoutException:
        detection = _fallback("timeout", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    except httpx.HTTPError:
        LOG.warning("call language request failed; using the fallback language")
        detection = _fallback("http", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    if response.status_code in (401, 403):
        mark_jev_auth_failed()
        LOG.warning("call language request was rejected; using the fallback language")
        detection = _fallback("auth", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    if response.status_code >= 400:
        LOG.warning(
            "call language request failed (HTTP %s); using the fallback language",
            response.status_code,
        )
        detection = _fallback(f"http_{response.status_code}", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    try:
        payload = response.json()
    except ValueError:
        LOG.warning("call language response was not JSON; using the fallback language")
        detection = _fallback("json", latency_ms=elapsed())
        _log_detection(detection, task)
        return detection
    choice, confidence = parse_jev_choice(payload, "language", default="")
    code = normalize_language_code(choice)
    if not code:
        detection = _fallback("invalid", latency_ms=elapsed(), confidence=confidence)
        _log_detection(detection, task)
        return detection
    if confidence < settings.language_min_confidence:
        detection = _fallback("low_confidence", latency_ms=elapsed(), confidence=confidence)
        _log_detection(detection, task)
        return detection
    detection = LanguageDetection(
        language=code,
        confidence=confidence,
        source="jev",
        latency_ms=elapsed(),
    )
    _log_detection(detection, task)
    return detection


async def resolve_phrases(
    settings: Settings,
    code: str,
) -> tuple[dict[str, str], str, str]:
    """Return ``(phrases, spoken_language, phrase_source)``.

    Spoken language is ``en`` when a translation was required and did not succeed.
    """
    lang = normalize_language_code(code) or "de"
    principal = principal_for_phrases(settings)
    if lang in STATIC_LANGS:
        return static_phrases(lang, principal=principal), lang, "table"
    LOG.info(
        "call language phrases=%s source=fixed_en (no free-form translation)",
        lang,
    )
    return static_phrases("en", principal=principal), "en", "fixed_en"


def disclosure_for_call(settings: Settings, call_language: str, phrases: dict[str, str]) -> str:
    """Fixed per-language AI disclosure. ``opening_line`` cannot replace it."""
    _ = settings, call_language  # phrase table is already language-specific
    return str(phrases.get("ai_notice") or "").strip()


def fallback_language(settings: Settings) -> str:
    """``FERNRUF_WHISPER_LANGUAGE`` when detection fails. Empty means German."""
    return normalize_language_code(settings.whisper_language or "") or "de"


def fish_voice_id_for(settings: Settings, code: str) -> tuple[str, str]:
    """Per-language ``FERNRUF_FISH_VOICE_ID_<LANG>``, else ``FISH_VOICE_ID``."""
    lang = (normalize_language_code(code) or code or "de").strip().lower()
    key = f"FERNRUF_FISH_VOICE_ID_{lang.upper()}"
    specific = os.environ.get(key, "").strip()
    source = "env" if specific else ""
    if not specific:
        specific = _dotenv_value(key)
        source = "dotenv" if specific else ""
    if specific:
        return specific, key
    return (settings.fish_voice_id or "").strip(), "FISH_VOICE_ID"


def _dotenv_value(key: str) -> str:
    override = os.environ.get("FERNRUF_ENV_FILE")
    if override is not None and override.strip().lower() in ("", "none", "off"):
        return ""
    path = Path(override) if override else Path(".env")
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return ""
    values = dotenv_values(path)
    return str(values.get(key) or "").strip()


async def prepare_call_settings(
    settings: Settings,
    task: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    language: str | None = None,
    detection_text: str | None = None,
) -> Settings:
    """Speak ``language`` when set; else local detection or optional Jev. Once per call.

    ``detection_text`` is what classifiers see. Callers that wrap the task in German
    labels pass the raw goal, context, and must_say here.
    """
    if getattr(settings, "_language_ready", False):
        return settings
    explicit = normalize_language_code(language or "")
    sample = (detection_text if detection_text is not None else task) or task
    if explicit:
        detection = LanguageDetection(
            language=explicit,
            confidence=1.0,
            source="argument",
            reason="language_argument",
        )
        _log_detection(detection, sample)
    else:
        detection = await detect_call_language(settings, sample, transport=transport)
    chosen = (
        detection.language
        if detection.source in {"jev", "argument", "local"}
        else fallback_language(settings)
    )
    phrases, spoken, phrase_source = await resolve_phrases(settings, chosen)
    if spoken != chosen:
        LOG.info(
            "call language spoken=%s detected=%s phrase_source=%s",
            spoken,
            chosen,
            phrase_source,
        )
    else:
        LOG.info("call language spoken=%s phrase_source=%s", spoken, phrase_source)
    # The language of this call drives Whisper. FERNRUF_WHISPER_LANGUAGE is only
    # the code used above when detection itself failed.
    phrases = dict(phrases)
    detected_label = (
        detection.language
        if detection.source in {"jev", "argument", "local"}
        else f"fallback:{detection.reason or detection.source}"
    )
    LOG.info(
        "call start detected_language=%s whisper_language=%s initial_prompt=%s",
        detected_label,
        spoken,
        phrases["whisper_initial_prompt"],
    )
    voice, voice_source = fish_voice_id_for(settings, spoken)
    LOG.info("call fish voice source=%s language=%s", voice_source, spoken)
    from fernruf.disclosure import spoken_privacy_line

    prepared = settings.model_copy(
        update={
            "call_language": spoken,
            "call_phrases": phrases,
            "disclosure_prefix": disclosure_for_call(settings, spoken, phrases),
            "spoken_privacy_line": spoken_privacy_line(settings, language=spoken),
            "filler_phrase": phrases["filler"],
            "llm_delay_apology_phrase": phrases["delay_apology"],
            "fish_voice_id": voice,
            "whisper_language": spoken,
        }
    )
    object.__setattr__(prepared, "_language_ready", True)
    object.__setattr__(prepared, "_detected_language_label", detected_label)
    return prepared
