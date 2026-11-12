from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

_OFF = ("0", "false", "no", "off", " FALSE ")


def _run(script: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    env["FERNRUF_UI_LANG"] = "en"
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _check(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


_PRESENT = textwrap.dedent(
    """
    from pathlib import Path

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QWindow
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from fernruf.desktop.app import CallModel, _load_window

    app = QApplication(["fernruf-present"])
    model = CallModel(Path("/tmp/fernruf-present-pref.json"))
    window = _load_window(model)
    assert window is not None

    def front(label):
        app.processEvents()
        assert window.isVisible(), label
        assert window.isActive(), label
        assert QGuiApplication.focusWindow() is window, label
        return window.visibility()

    def event(call_id, state, number):
        model.apply_lifecycle(
            {"state": state, "call_id": call_id, "to_masked": number, "summary": None}
        )
        app.processEvents()

    def item(name):
        found = window.findChild(QQuickItem, name)
        assert found is not None, name
        return found

    def click(name):
        target = item(name)
        assert target.isVisible(), name
        point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
        QTest.mouseClick(
            window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point,
        )
        app.processEvents()

    assert window.isVisible() is False
    event("call-1", "dialing", "+49 30 23125 100")
    assert front("dialing") == QWindow.Visibility.Windowed
    assert model.number == "+49 30 23125 100"

    window.hide()
    app.processEvents()
    event("call-1", "ringing", "+49 30 23125 100")
    assert window.isVisible() is False, "a later event for the same call stays hidden"

    event("call-2", "queued", "+49 30 23125 101")
    assert front("queued") == QWindow.Visibility.Windowed
    assert model.number == "+49 30 23125 101"

    window.showMinimized()
    app.processEvents()
    assert window.visibility() == QWindow.Visibility.Minimized
    event("call-2", "answered", "+49 30 23125 101")
    assert window.visibility() == QWindow.Visibility.Minimized, "the same call stays minimized"

    event("call-3", "ringing", "+49 30 23125 102")
    assert front("first event for a new call") == QWindow.Visibility.Windowed
    assert model.number == "+49 30 23125 102"

    window.hide()
    app.processEvents()
    event("call-4", "dialing", "+49 30 23125 103")
    assert front("dialing after close") == QWindow.Visibility.Windowed

    window.showMaximized()
    app.processEvents()
    event("call-5", "dialing", "+49 30 23125 104")
    assert front("already open") == QWindow.Visibility.Maximized
    assert model.number == "+49 30 23125 104"
    assert window.property("unitRadius") == 0

    event("call-5", "finalized", "+49 30 23125 104")
    assert window.isVisible() is True
    assert window.visibility() == QWindow.Visibility.Maximized
    assert model.state == "finalized"

    order = []
    for name in ("minimizeWindow", "maximizeWindow", "closeWindow"):
        target = item(name)
        assert target.isVisible(), name
        order.append(target.mapToScene(QPointF(1, 1)).x())
        assert target.property("tip"), name
    assert order[0] < order[1] < order[2]

    window.showNormal()
    app.processEvents()
    assert item("maximizeWindow").property("iconName") == "window-maximize"
    assert item("maximizeWindow").property("tip") == "Maximize"
    click("maximizeWindow")
    assert window.visibility() == QWindow.Visibility.Maximized
    assert window.property("unitRadius") == 0
    assert item("maximizeWindow").property("iconName") == "window-restore"
    assert item("maximizeWindow").property("tip") == "Restore"
    click("maximizeWindow")
    assert window.visibility() == QWindow.Visibility.Windowed
    assert window.property("unitRadius") == 24

    event("call-6", "answered", "+49 30 23125 105")
    click("minimizeWindow")
    assert window.visibility() == QWindow.Visibility.Minimized
    assert model.state == "answered"
    window.showNormal()
    app.processEvents()
    click("closeWindow")
    assert window.isVisible() is False
    assert model.state == "answered"
    window.show()
    app.processEvents()
    assert window.isVisible() is True
    """
)


def test_a_new_call_shows_the_window_and_the_controls_work() -> None:
    _check(_run(_PRESENT))


_MANUAL = textwrap.dedent(
    """
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from fernruf.desktop.app import CallModel, _load_window

    app = QApplication(["fernruf-present-off"])
    model = CallModel(Path("/tmp/fernruf-present-off-pref.json"))
    window = _load_window(model)
    assert window is not None
    model.apply_lifecycle(
        {
            "state": "dialing",
            "call_id": "call-9",
            "to_masked": "+49 30 23125 109",
            "summary": None,
        }
    )
    app.processEvents()
    assert window.isVisible() is False
    window.show()
    app.processEvents()
    assert window.isVisible() is True
    model.apply_lifecycle(
        {
            "state": "queued",
            "call_id": "call-10",
            "to_masked": "+49 30 23125 110",
            "summary": None,
        }
    )
    app.processEvents()
    assert window.isVisible() is True
    assert model.number == "+49 30 23125 110"
    assert model.state == "queued"
    """
)


@pytest.mark.parametrize("value", _OFF)
def test_auto_show_off_leaves_the_window_hidden_until_opened(value: str) -> None:
    _check(_run(_MANUAL, FERNRUF_MONITOR_AUTO_SHOW=value))
