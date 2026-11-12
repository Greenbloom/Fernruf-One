"""The call window marks visible copy for translation and ships German."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from fernruf.desktop.i18n import language_code

ROOT = Path(__file__).resolve().parents[1]
QML = ROOT / "fernruf" / "desktop" / "MonitorPlaceholder.qml"
APP = ROOT / "fernruf" / "desktop" / "app.py"
TS = ROOT / "fernruf" / "desktop" / "i18n" / "fernruf_de.ts"
QM = ROOT / "fernruf" / "desktop" / "i18n" / "fernruf_de.qm"

_QSTR = re.compile(r'qsTr\(\s*"((?:\\.|[^"\\])*)"\s*\)')
_PYSTR = re.compile(r'(?<![\w.])tr\(\s*"((?:\\.|[^"\\])*)"\s*\)')
_LITERAL = re.compile(r'"((?:\\.|[^"\\])*)"')
_KEYPAD = re.compile(r"^[0-9A-Z+*#]{0,4}$")


def _sources(pattern: re.Pattern[str], path: Path) -> set[str]:
    return set(pattern.findall(path.read_text(encoding="utf-8")))


def _unwrapped_visible(qml: str) -> list[str]:
    missing: list[str] = []
    for match in _LITERAL.finditer(qml):
        value = match.group(1)
        if not any(ch.isalpha() for ch in value) or _KEYPAD.fullmatch(value):
            continue
        prefix = qml[max(0, match.start() - 12) : match.start()]
        if prefix.rstrip().endswith("qsTr("):
            continue
        window = qml[max(0, match.start() - 160) : match.start()]
        props = list(re.finditer(r"\b(?:text|caption|label|title)\s*:", window))
        if not props:
            continue
        between = window[props[-1].end() :]
        if "qsTr(" in between or re.search(r"[!=]==\s*$", between):
            continue
        if re.search(
            r"\b(?:color|font|source|iconName|iconTint|fill|border|objectName)\b", between
        ):
            continue
        missing.append(value)
    return missing


def _catalog() -> dict[tuple[str, str], str]:
    tree = ET.parse(TS)
    found: dict[tuple[str, str], str] = {}
    for context in tree.getroot().findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            translation = message.findtext("translation") or ""
            found[(name, source)] = translation
    return found


def test_a_bare_label_is_rejected_and_a_marked_label_is_not() -> None:
    assert _unwrapped_visible('Label { text: "Hello" }') == ["Hello"]
    assert _unwrapped_visible('Label { text: qsTr("Hello") }') == []
    assert _unwrapped_visible('text: "Hello"\nobjectName: "maximizeWindow"') == ["Hello"]


def test_visible_window_strings_are_marked_for_translation() -> None:
    missing = _unwrapped_visible(QML.read_text(encoding="utf-8"))
    assert missing == []


def test_german_catalog_covers_every_marked_string() -> None:
    catalog = _catalog()
    qml = {("MonitorPlaceholder", source) for source in _sources(_QSTR, QML)}
    python = {("CallMonitor", source) for source in _sources(_PYSTR, APP)}
    marked = qml | python
    assert marked
    missing = sorted(source for source in marked if not catalog.get(source, "").strip())
    assert missing == []


def test_language_override_uses_german_or_english() -> None:
    assert language_code("de", "en_US") == "de"
    assert language_code("de-DE", "en_US") == "de"
    assert language_code("fr", "de_DE") == "en"
    assert language_code("", "de_DE") == "de"
    assert language_code("", "en_GB") == "en"


def test_committed_german_catalog_loads_every_string() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    env["FERNRUF_UI_LANG"] = "de"
    script = r"""
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtWidgets import QApplication

root = Path(%r)
ts = root / "fernruf/desktop/i18n/fernruf_de.ts"
qm = root / "fernruf/desktop/i18n/fernruf_de.qm"
fresh = root / "fernruf/desktop/i18n/fernruf_de.fresh.qm"
lrelease = Path(sys.executable).with_name("pyside6-lrelease")
subprocess.run([str(lrelease), str(ts), "-qm", str(fresh)], check=True)
app = QApplication([])
shipped = QTranslator()
built = QTranslator()
assert shipped.load(str(qm))
assert built.load(str(fresh))
for context in ET.parse(ts).getroot().findall("context"):
    name = context.findtext("name") or ""
    for message in context.findall("message"):
        source = message.findtext("source") or ""
        german = message.findtext("translation") or ""
        app.installTranslator(shipped)
        assert QCoreApplication.translate(name, source) == german, source
        app.removeTranslator(shipped)
        app.installTranslator(built)
        assert QCoreApplication.translate(name, source) == german, source
        app.removeTranslator(built)
fresh.unlink()
""" % str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
