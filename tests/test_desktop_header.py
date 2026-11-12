"""The transcript status stays left of the window controls."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

_HEADER = textwrap.dedent(
    """
    import os
    from pathlib import Path

    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QWindow
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from fernruf.desktop.app import CallModel, _load_window
    from fernruf.desktop.i18n import install_translator

    lang = os.environ["FERNRUF_UI_LANG"]
    copy = {
        "en": {
            "title": "Live transcript",
            "dialing": "Waiting for the call",
            "ringing": "Waiting for the call",
            "off": "Listening off",
            "on": "Listening on · local playback",
            "ended": "Summary is being written…",
            "finalized": "Finished",
            "error": "Connection lost",
        },
        "de": {
            "title": "Live-Transkript",
            "dialing": "Wartet auf Verbindung",
            "ringing": "Wartet auf Verbindung",
            "off": "Mithören aus",
            "on": "Mithören an · lokale Wiedergabe",
            "ended": "Zusammenfassung wird erstellt…",
            "finalized": "Abgeschlossen",
            "error": "Verbindung unterbrochen",
        },
    }[lang]
    longest = max(copy.values(), key=len)

    app = QApplication(["fernruf-header"])
    assert install_translator(app) == lang
    model = CallModel(Path(f"/tmp/fernruf-header-{lang}.json"))
    window = _load_window(model)
    assert window is not None
    window.showNormal()
    model.apply_lifecycle(
        {
            "state": "idle",
            "call_id": "call-header",
            "to_masked": "+49 30 23125 100",
            "summary": None,
        }
    )
    app.processEvents()

    def scene_rect(node):
        origin = node.mapToScene(QPointF(0, 0))
        return QRectF(origin.x(), origin.y(), node.width(), node.height())

    def overlaps(left, right):
        hit = scene_rect(left).intersected(scene_rect(right))
        return hit.width() > 0.5 and hit.height() > 0.5

    def item(name):
        found = window.findChild(QQuickItem, name)
        assert found is not None and found.isVisible(), name
        return found

    def find_text(text):
        matches = []
        for child in window.findChildren(QQuickItem):
            if child.property("text") != text or not child.isVisible():
                continue
            if scene_rect(child).y() <= 80:
                matches.append(child)
        assert len(matches) == 1, (text, len(matches))
        return matches[0]

    def place(mode):
        if mode == "maximized":
            window.showMaximized()
            app.processEvents()
            assert window.visibility() == QWindow.Visibility.Maximized
            assert window.property("unitRadius") == 0
            return
        window.showNormal()
        app.processEvents()
        assert window.visibility() == QWindow.Visibility.Windowed
        if mode == "narrow":
            window.setWidth(760)
            app.processEvents()
            assert window.width() == 760
        else:
            assert window.width() == 1080

    def check(mode, state, text):
        title = find_text(copy["title"])
        buttons = [item(name) for name in ("minimizeWindow", "maximizeWindow", "closeWindow")]
        for button in buttons:
            assert not overlaps(title, button), (mode, state, "title")
        order = [scene_rect(title).x()]
        if text:
            label = find_text(text)
            order.append(scene_rect(label).x())
            for button in buttons:
                assert not overlaps(label, button), (
                    mode,
                    state,
                    text,
                    scene_rect(label),
                    scene_rect(button),
                )
            gap = scene_rect(buttons[0]).left() - scene_rect(label).right()
            assert 8 <= gap <= 20, (mode, state, text, gap)
            title_gap = scene_rect(label).left() - scene_rect(title).right()
            assert title_gap >= 8, (mode, state, text, title_gap)
            room = scene_rect(buttons[0]).left() - scene_rect(title).right()
            assert label.width() <= room + 1, (mode, state, text, label.width(), room)
            slack = label.implicitWidth() - label.width()
            if slack > 16:
                assert label.property("truncated") is True, (mode, state, text, slack)
            if mode == "narrow" and text == longest:
                assert slack > 16, (text, label.width(), label.implicitWidth())
        order.extend(scene_rect(button).x() for button in buttons)
        assert all(order[i] < order[i + 1] for i in range(len(order) - 1)), (mode, state, order)

    cases = [
        ("idle", "", False),
        ("queued", "", False),
        ("dialing", "dialing", False),
        ("ringing", "ringing", False),
        ("answered", "off", False),
        ("answered", "on", True),
        ("ended", "ended", False),
        ("finalized", "finalized", False),
        ("answered", "error", False),
    ]
    for mode in ("normal", "narrow", "maximized"):
        for state, key, listen in cases:
            place(mode)
            if key == "error":
                model.set_error("monitor down")
                model.set_state(state)
                model.setListening(False)
                text = copy["error"]
            else:
                model.set_error("")
                model.set_state(state)
                model.setListening(listen)
                text = copy[key] if key else ""
            app.processEvents()
            assert window.chatStatus() == text, (mode, state, window.chatStatus(), text)
            check(mode, state, text)

    window.showNormal()
    app.processEvents()
    toggle = item("transcriptToggle")
    point = toggle.mapToScene(QPointF(toggle.width() / 2, toggle.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()
    assert window.property("chatOpen") is False
    panel = window.findChild(QQuickItem, "transcriptPanel")
    assert panel is not None and panel.isVisible() is False
    for name in ("minimizeWindow", "maximizeWindow", "closeWindow"):
        assert item(name).isVisible()
    number = find_text(model.number)
    for name in ("minimizeWindow", "maximizeWindow", "closeWindow"):
        assert not overlaps(number, item(name)), name
    """
)


def _run(lang: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    env["FERNRUF_UI_LANG"] = lang
    return subprocess.run(
        [sys.executable, "-c", _HEADER],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("lang", ["en", "de"])
def test_status_label_stays_left_of_the_window_controls(lang: str) -> None:
    proc = _run(lang)
    assert proc.returncode == 0, proc.stdout + proc.stderr
