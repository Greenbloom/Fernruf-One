"""Tray client. Listening is off until the local preference says otherwise.

The window file is ``MonitorPlaceholder.qml``. That is the file to replace
when the mockup arrives. This module only feeds it data and plays audio.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fernruf.desktop.prefs import default_pref_path, load_listen_pref, save_listen_pref
from fernruf.monitor.protocol import unpack_audio


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    smoke = "--smoke" in args
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Install the desktop extra: uv sync --extra desktop", file=sys.stderr)
        return 1
    if smoke and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv if argv is None else ["fernruf-monitor", *args])
    pref_path = default_pref_path()
    model = CallModel(pref_path)
    window = _load_window(model)
    if window is None:
        print("placeholder window failed to load", file=sys.stderr)
        return 1
    if not smoke:
        _tray(app, window)
        token = os.environ.get("FERNRUF_MONITOR_TOKEN") or os.environ.get("FERNRUF_MCP_TOKEN") or ""
        url = os.environ.get("FERNRUF_MONITOR_URL") or "ws://127.0.0.1:8791"
        if token:
            _connect(model, url, token)
        else:
            model.set_state("no token")
    else:
        model.apply_lifecycle(
            {
                "state": "dialing",
                "to_masked": "••••0000",
                "summary": None,
            }
        )
        QTimer.singleShot(50, app.quit)
    return app.exec()


def _load_window(model):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    qml = Path(__file__).with_name("MonitorPlaceholder.qml")
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("monitor", model)
    engine.rootContext().setContextProperty("lineModel", model.line_model)
    engine.load(QUrl.fromLocalFile(str(qml)))
    model._engine = engine
    roots = engine.rootObjects()
    return roots[0] if roots else None


def _tray(app, window) -> None:
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    pixmap = QPixmap(16, 16)
    pixmap.fill()
    icon = QIcon.fromTheme("call-start")
    tray = QSystemTrayIcon(icon if not icon.isNull() else QIcon(pixmap))
    tray.setToolTip("Fernruf")
    menu = QMenu()
    menu.addAction("Show", window.show)
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.show()
    app._fernruf_tray = tray


def _connect(model, url: str, token: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtNetwork import QNetworkRequest
    from PySide6.QtWebSockets import QWebSocket

    socket = QWebSocket()
    model._socket = socket

    def on_text(message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == "lifecycle":
            model.apply_lifecycle(msg)
        elif kind == "transcript":
            model.apply_transcript(msg)

    def on_binary(message) -> None:
        try:
            frame = unpack_audio(bytes(message))
        except ValueError:
            return
        model.play(frame)

    def opened() -> None:
        model.send_listen()

    socket.textMessageReceived.connect(on_text)
    socket.binaryMessageReceived.connect(on_binary)
    socket.connected.connect(opened)
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
    socket.open(request)


class CallModel:
    """Built lazily so importing this module does not require Qt."""

    def __new__(cls, pref_path: Path):
        from PySide6.QtCore import Property, QObject, QStringListModel, QTimer, Signal, Slot

        class _Model(QObject):
            changed = Signal()
            callStarted = Signal()

            def __init__(self, path: Path) -> None:
                super().__init__()
                self._path = path
                pref = load_listen_pref(path)
                self._listening = bool(pref["listen"])
                self._volume = float(pref["volume"])
                self._muted = bool(pref["muted"])
                self._number = ""
                self._state = "idle"
                self._elapsed = "00:00"
                self._summary = ""
                self._answered_at: float | None = None
                self._timing = False
                self._lines: list[str] = []
                self.line_model = QStringListModel()
                self._sinks: dict[str, object] = {}
                self._socket = None
                self._clock = QTimer(self)
                self._clock.setInterval(200)
                self._clock.timeout.connect(self._tick)
                self._clock.start()

            def _store(self) -> None:
                save_listen_pref(
                    self._path,
                    listen=self._listening,
                    volume=self._volume,
                    muted=self._muted,
                )

            def send_listen(self) -> None:
                socket = self._socket
                if socket is None:
                    return
                socket.sendTextMessage(
                    json.dumps({"v": 1, "type": "listen", "enabled": self._listening})
                )

            @Property(str, notify=changed)
            def number(self) -> str:
                return self._number

            @Property(str, notify=changed)
            def state(self) -> str:
                return self._state

            @Property(str, notify=changed)
            def elapsed(self) -> str:
                return self._elapsed

            @Property(str, notify=changed)
            def summaryLine(self) -> str:
                return self._summary

            @Property(bool, notify=changed)
            def listening(self) -> bool:
                return self._listening

            @Property(float, notify=changed)
            def volume(self) -> float:
                return self._volume

            @Property(bool, notify=changed)
            def muted(self) -> bool:
                return self._muted

            @Slot(bool)
            def setListening(self, enabled: bool) -> None:
                self._listening = bool(enabled)
                self._store()
                self.send_listen()
                if not self._listening:
                    self._close_sinks()
                self.changed.emit()

            @Slot(float)
            def setVolume(self, value: float) -> None:
                self._volume = min(1.0, max(0.0, float(value)))
                self._store()
                self._apply_volume()
                self.changed.emit()

            @Slot(bool)
            def setMuted(self, muted: bool) -> None:
                self._muted = bool(muted)
                self._store()
                self._apply_volume()
                self.changed.emit()

            def set_state(self, state: str) -> None:
                self._state = state
                self.changed.emit()

            def apply_lifecycle(self, msg: dict) -> None:
                self._number = str(msg.get("to_masked") or "")
                state = str(msg.get("state") or "")
                self._state = state
                if state == "dialing":
                    self._lines = []
                    self.line_model.setStringList([])
                    self._summary = ""
                    self._answered_at = None
                    self._timing = False
                    self._elapsed = "00:00"
                    self.callStarted.emit()
                elif state == "answered":
                    self._answered_at = time.monotonic()
                    self._timing = True
                elif state in ("ended", "finalized"):
                    self._timing = False
                    self._tick()
                summary = msg.get("summary")
                if isinstance(summary, dict):
                    self._summary = str(summary.get("one_line") or "")
                self.changed.emit()

            def apply_transcript(self, msg: dict) -> None:
                speaker = str(msg.get("speaker") or "")
                text = str(msg.get("text") or "").strip()
                if speaker not in ("agent", "callee") or not text:
                    return
                label = "Agent" if speaker == "agent" else "Callee"
                self._lines.append(f"{label}: {text}")
                self.line_model.setStringList(self._lines)

            def play(self, frame: dict) -> None:
                if not self._listening or self._muted:
                    return
                pcm = frame.get("pcm") or b""
                if not pcm:
                    return
                direction = str(frame.get("direction") or "")
                opened = self._sink(direction, int(frame.get("sample_rate") or 16000))
                if opened is None:
                    return
                opened[0].write(pcm)

            def _sink(self, direction: str, sample_rate: int):
                from PySide6.QtMultimedia import QAudioFormat, QAudioSink

                current = self._sinks.get(direction)
                if current is not None and current[0] == sample_rate:
                    return current[1:]
                fmt = QAudioFormat()
                fmt.setSampleRate(sample_rate)
                fmt.setChannelCount(1)
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                audio = QAudioSink(fmt)
                audio.setVolume(0.0 if self._muted else self._volume)
                device = audio.start()
                if device is None:
                    return None
                self._sinks[direction] = (sample_rate, device, audio)
                return device, audio

            def _apply_volume(self) -> None:
                level = 0.0 if self._muted else self._volume
                for _rate, _device, audio in self._sinks.values():
                    audio.setVolume(level)

            def _close_sinks(self) -> None:
                for _rate, _device, audio in self._sinks.values():
                    audio.stop()
                self._sinks.clear()

            def _tick(self) -> None:
                if not self._timing or self._answered_at is None:
                    return
                seconds = max(0, int(time.monotonic() - self._answered_at))
                text = f"{seconds // 60:02d}:{seconds % 60:02d}"
                if text != self._elapsed:
                    self._elapsed = text
                    self.changed.emit()

        _Model.__name__ = "CallModel"
        return _Model(pref_path)
