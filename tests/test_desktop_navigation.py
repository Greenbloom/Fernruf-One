"""Leaving the in-call page still has a way back to the transcript."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")


def test_transcript_off_then_keypad_then_back_then_transcript_on() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FERNRUF_ENV_FILE"] = "none"
    script = textwrap.dedent(
        """
        from pathlib import Path

        from PySide6.QtCore import QPointF, Qt, QTimer
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from fernruf.desktop.app import CallModel, _load_window

        app = QApplication(["fernruf-navigation"])
        model = CallModel(Path("/tmp/fernruf-navigation-pref.json"))
        model.apply_lifecycle(
            {"state": "answered", "to_masked": "+49 30 0000 0000", "summary": None}
        )
        window = _load_window(model)
        assert window is not None
        window.show()
        app.processEvents()

        def item(name):
            found = window.findChild(QQuickItem, name)
            assert found is not None, name
            return found

        def click(name):
            target = item(name)
            assert target.isVisible(), name
            point = target.mapToScene(
                QPointF(target.width() / 2, target.height() / 2)
            ).toPoint()
            QTest.mouseClick(
                window,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                point,
            )
            app.processEvents()

        assert window.property("phonePage") == 1
        assert window.property("chatOpen") is True
        assert item("transcriptPanel").isVisible()

        click("transcriptToggle")
        assert window.property("chatOpen") is False
        assert item("transcriptPanel").isVisible() is False

        click("openKeypad")
        assert window.property("phonePage") == 0
        assert item("backToCall").isVisible()
        assert item("transcriptOnDialer").isVisible()

        window.requestActivate()
        app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert window.property("phonePage") == 1, "Escape returns to the in-call page"

        click("openKeypad")
        assert window.property("phonePage") == 0
        click("backToCall")
        assert window.property("phonePage") == 1
        assert window.property("chatOpen") is False

        click("transcriptToggle")
        assert window.property("chatOpen") is True
        assert item("transcriptPanel").isVisible()

        click("openKeypad")
        click("transcriptOnDialer")
        assert window.property("chatOpen") is False
        assert item("transcriptPanel").isVisible() is False
        click("transcriptOnDialer")
        assert window.property("chatOpen") is True
        assert item("transcriptPanel").isVisible()
        QTimer.singleShot(0, app.quit)
        raise SystemExit(app.exec())
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
