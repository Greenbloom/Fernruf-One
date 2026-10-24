"""Principal name, fixed disclosure templates, and identity helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fernruf.settings import Settings

# Only for comparing against .env.example; not spoken until the operator replaces it.
PRINCIPAL_ENV_PLACEHOLDER = "Alex Example"

PHRASE_TEMPLATE_KEYS = (
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

# Fixed per-language lines. {principal} is substituted at runtime; the LLM cannot change these.
PHRASE_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "ai_notice": "Hallo, hier ist ein KI-Assistent, der im Auftrag von {principal} anruft.",
        "consent_question": "Darf ich das Gespräch zur Dokumentation aufzeichnen?",
        "filler": "Einen Moment…",
        "goodbye": "Auf Wiederhören.",
        "llm_failure_apology": (
            "Entschuldigung, ich habe gerade ein technisches Problem mit der KI-Verbindung. "
            "Einen Moment bitte."
        ),
        "llm_failure_goodbye": (
            "Entschuldigung, die Sprachverbindung zur KI funktioniert leider nicht. "
            "Bitte versuchen Sie es später erneut. Auf Wiederhören."
        ),
        "whisper_initial_prompt": "Telefonat auf Deutsch.",
        "delay_apology": "Entschuldigung, ich habe gerade eine kleine Verzögerung.",
        "identity_line": "Ich bin ein KI-Assistent von {principal}.",
        "reask": (
            "Entschuldigung, das habe ich nicht verstanden. "
            "Könnten Sie das bitte noch einmal sagen?"
        ),
        "farewell_example": (
            "Vielen Dank, dann meldet sich {principal} um 16 Uhr. Auf Wiederhören."
        ),
        "privacy_notice": (
            "Kurzer Hinweis: Sprache und Gesprächsinhalte können von KI-Diensten verarbeitet werden."
        ),
    },
    "en": {
        "ai_notice": "Hello, this is an AI assistant calling on behalf of {principal}.",
        "consent_question": "May I record this call for documentation?",
        "filler": "One moment…",
        "goodbye": "Goodbye.",
        "llm_failure_apology": (
            "Sorry, I'm having a technical problem with the AI connection. One moment please."
        ),
        "llm_failure_goodbye": (
            "Sorry, the voice connection to the AI isn't working. Please try again later. Goodbye."
        ),
        "whisper_initial_prompt": "Phone call in English.",
        "delay_apology": "Sorry, I'm having a short delay.",
        "identity_line": "I am an AI assistant of {principal}.",
        "reask": "Sorry, I didn't catch that. Could you say that once more?",
        "farewell_example": "Thank you, then {principal} will get in touch at 7 p.m. Goodbye.",
        "privacy_notice": (
            "Brief notice: speech and call content may be processed by AI services."
        ),
    },
    "zh": {
        "ai_notice": "您好，这里是人工智能助手，代表 {principal} 来电。",
        "consent_question": "为了做记录，我可以录下这通电话吗？",
        "filler": "请稍等…",
        "goodbye": "再见。",
        "llm_failure_apology": "抱歉，我这边的人工智能连接出了技术问题。请稍等。",
        "llm_failure_goodbye": "抱歉，与人工智能的语音连接无法使用。请稍后再试。再见。",
        "whisper_initial_prompt": "这是一通中文电话。",
        "delay_apology": "抱歉，我这边稍微延迟了一下。",
        "identity_line": "我是 {principal} 的人工智能助手。",
        "reask": "抱歉，我没听清。请再说一次好吗？",
        "farewell_example": "谢谢，那么 {principal} 会在下午四点跟您联系。再见。",
        "privacy_notice": "简短说明：通话语音与内容可能会由人工智能服务处理。",
    },
}


def principal_name_is_placeholder(raw: str) -> bool:
    """True when unset or still the documented .env.example value."""
    text = (raw or "").strip()
    if not text:
        return True
    return text.casefold() == PRINCIPAL_ENV_PLACEHOLDER.casefold()


PRINCIPAL_PLACEHOLDER_MESSAGE = (
    "Set FERNRUF_PRINCIPAL_NAME to the person or organization this agent represents. "
    f'The example value "{PRINCIPAL_ENV_PLACEHOLDER}" from .env.example is not allowed for real calls.'
)


def settings_allow_principal_placeholder(settings: Settings) -> bool:
    """Only non-dial / offline voice paths may keep the .env.example principal name."""
    return bool(settings.voice_mock or settings.m3_simulate)


def dial_blocked_placeholder_reason(settings: Settings) -> str | None:
    if settings_allow_principal_placeholder(settings):
        return None
    if principal_name_is_placeholder(settings.principal_name):
        return PRINCIPAL_PLACEHOLDER_MESSAGE
    return None


def require_principal_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise ValueError(
            "FERNRUF_PRINCIPAL_NAME is required (the person or organization the AI represents)."
        )
    if len(name) > 120:
        raise ValueError("FERNRUF_PRINCIPAL_NAME is too long.")
    return name


def format_phrase_templates(lang: str, principal: str) -> dict[str, str]:
    code = lang if lang in PHRASE_TEMPLATES else "de"
    principal = require_principal_name(principal)
    out: dict[str, str] = {}
    for key in PHRASE_TEMPLATE_KEYS:
        out[key] = PHRASE_TEMPLATES[code][key].format(principal=principal)
    return out


def german_ai_notice(principal: str) -> str:
    return format_phrase_templates("de", principal)["ai_notice"]


_PRINCIPAL_TOKEN_STOPWORDS = frozenset(
    {
        "am",
        "an",
        "at",
        "de",
        "der",
        "die",
        "das",
        "du",
        "im",
        "in",
        "of",
        "on",
        "the",
        "und",
        "von",
        "zu",
        "for",
        "and",
        "la",
        "le",
        "les",
    }
)


_TITLE_PART = re.compile(
    r"^(?:dr|prof|mr|mrs|ms|herr|frau)\.?$",
    re.IGNORECASE,
)


def _name_parts_without_titles(principal: str) -> tuple[str, ...]:
    parts = [p for p in re.split(r"\s+", (principal or "").strip()) if p]
    cleaned: list[str] = []
    for part in parts:
        bare = part.rstrip(".")
        if _TITLE_PART.match(bare):
            continue
        cleaned.append(part)
    return tuple(cleaned)


def principal_tokens(principal: str) -> tuple[str, ...]:
    """Full principal name, name without titles, and first given name."""
    text = (principal or "").strip()
    if not text:
        return ()
    out: list[str] = [text]
    parts = _name_parts_without_titles(text)
    if parts:
        short = " ".join(parts)
        if short != text:
            out.append(short)
        first = parts[0]
        if len(first) >= 2 and first.casefold() not in _PRINCIPAL_TOKEN_STOPWORDS:
            out.append(first)
    return tuple(dict.fromkeys(out))


def build_identity_replacement(principal: str) -> str:
    return format_phrase_templates("de", principal)["identity_line"]
