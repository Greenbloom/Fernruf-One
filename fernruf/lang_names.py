"""Spoken-language names. Shared by the call locale and the Jev mood request."""

from __future__ import annotations

LANGUAGE_NAMES: dict[str, str] = {
    "de": "Deutsch",
    "en": "English",
    "zh": "Chinesisch",
    "fr": "Französisch",
    "es": "Spanisch",
    "it": "Italienisch",
    "tr": "Türkisch",
    "pl": "Polnisch",
    "ru": "Russisch",
    "ar": "Arabisch",
    "ja": "Japanisch",
    "nl": "Niederländisch",
    "pt": "Portugiesisch",
    "uk": "Ukrainisch",
}


def language_name(code: str) -> str:
    lang = (code or "de").strip().lower() or "de"
    return LANGUAGE_NAMES.get(lang, lang)
