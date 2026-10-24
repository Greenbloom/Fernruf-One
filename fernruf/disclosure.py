"""Fixed spoken AI disclosure and optional privacy notice."""

from __future__ import annotations

from fernruf.principal import format_phrase_templates, german_ai_notice, require_principal_name
from fernruf.settings import Settings


def spoken_ai_disclosure(settings: Settings) -> str:
    """Disclosure text used at the start of every real or simulated call."""
    return german_ai_notice(settings.require_principal())


def spoken_privacy_line(settings: Settings, *, language: str = "de") -> str:
    """Fixed per-language privacy notice when enabled; empty when disabled."""
    if not settings.spoken_privacy_enabled:
        return ""
    lang = (language or "de").strip().lower()[:2]
    if lang not in ("de", "en", "zh"):
        lang = "en"
    principal = require_principal_name(settings.principal_name)
    phrases = format_phrase_templates(lang, principal)
    return (phrases.get("privacy_notice") or "").strip()
