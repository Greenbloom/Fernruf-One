"""Window language. English is the source text. German is the shipped translation."""

from __future__ import annotations

import os
from pathlib import Path

UI_LANG_ENV = "FERNRUF_UI_LANG"
_QM = Path(__file__).with_name("i18n") / "fernruf_de.qm"


def language_code(requested: str, system: str) -> str:
    """``de`` when the override or the system language is German. Otherwise English."""
    raw = requested.strip().lower().replace("_", "-")
    if raw:
        code = raw.split("-", 1)[0]
        return "de" if code == "de" else "en"
    system_code = system.strip().lower().split("_", 1)[0]
    return "de" if system_code == "de" else "en"


def install_translator(app) -> str:
    """Install the German catalog when that language is selected. Return the code used."""
    from PySide6.QtCore import QLocale, QTranslator

    code = language_code(os.environ.get(UI_LANG_ENV, ""), QLocale.system().name())
    if code != "de":
        return "en"
    translator = QTranslator(app)
    if not translator.load(str(_QM)):
        return "en"
    app.installTranslator(translator)
    app._fernruf_translator = translator
    return "de"


def tr(text: str) -> str:
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.translate("CallMonitor", text)
