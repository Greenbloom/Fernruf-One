"""Tray client for one local call.

The window is ``MonitorPlaceholder.qml``. This module feeds it and plays
audio on this computer. It cannot speak into the call or hang it up.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fernruf.desktop.choices import (
    HANGUP_ENABLED,
    SEND_DTMF,
    SHOW_RECORD_BUTTON,
    SHOW_TRAY_ICON,
    USE_OPAQUE_FALLBACK,
    listen_from_disk,
    listen_to_store,
    monitor_auto_show,
)
from fernruf.desktop.i18n import install_translator, tr
from fernruf.desktop.lines import make_line_model
from fernruf.desktop.prefs import default_pref_path, load_listen_pref, save_listen_pref
from fernruf.monitor.pace import MONITOR_RATE, PlaybackPacer
from fernruf.monitor.protocol import unpack_audio


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    smoke = "--smoke" in args
    shot: str | None = None
    if "--screenshot" in args:
        at = args.index("--screenshot")
        if at + 1 >= len(args):
            print("pass a path after --screenshot", file=sys.stderr)
            return 2
        shot = args[at + 1]
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Install the desktop extra: uv sync --extra desktop", file=sys.stderr)
        return 1
    if (smoke or shot) and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    _enable_alpha()
    app = QApplication(sys.argv if argv is None else ["fernruf-monitor", *args])
    install_translator(app)
    pref_path = Path("/tmp/fernruf-monitor-shot.json") if shot else default_pref_path()
    model = CallModel(pref_path)
    window = _load_window(model)
    if window is None:
        print("call window failed to load", file=sys.stderr)
        return 1
    if shot:

        def fire() -> None:
            window.show()
            model.show_example()

        def shoot() -> None:
            screen = window.screen()
            image = screen.grabWindow(window.winId()) if screen is not None else None
            if image is None or image.width() < 100:
                print("screenshot was empty", file=sys.stderr)
                app.exit(1)
                return
            image.save(shot)
            app.quit()

        QTimer.singleShot(0, fire)
        QTimer.singleShot(800, shoot)
    elif not smoke:
        if SHOW_TRAY_ICON:
            _tray(app, window)
        token = os.environ.get("FERNRUF_MONITOR_TOKEN") or os.environ.get("FERNRUF_MCP_TOKEN") or ""
        url = os.environ.get("FERNRUF_MONITOR_URL") or "ws://127.0.0.1:8791"
        if token:
            _connect(model, url, token)
        else:
            model.set_error(tr("No token. Set FERNRUF_MONITOR_TOKEN or FERNRUF_MCP_TOKEN."))
    else:
        model.apply_lifecycle(
            {"state": "dialing", "to_masked": "+49 30 0000 0000", "summary": None}
        )
        QTimer.singleShot(50, app.quit)
    return app.exec()


def _enable_alpha() -> None:
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)


def _new_frame(opaque: bool):
    from PySide6.QtCore import Property, QObject, Signal

    kind = getattr(_new_frame, "kind", None)
    if kind is None:

        class Frame(QObject):
            opaqueChanged = Signal()

            def __init__(self, value: bool) -> None:
                super().__init__()
                self._opaque = bool(value)

            def read_opaque(self) -> bool:
                return self._opaque

            def set_opaque(self, value: bool) -> None:
                value = bool(value)
                if value == self._opaque:
                    return
                self._opaque = value
                self.opaqueChanged.emit()

            opaque = Property(bool, read_opaque, notify=opaqueChanged)

        kind = Frame
        _new_frame.kind = kind
    return kind(opaque)


def _load_window(model):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    _enable_alpha()
    qml = Path(__file__).with_name("MonitorPlaceholder.qml")
    engine = QQmlApplicationEngine()
    frame = _new_frame(USE_OPAQUE_FALLBACK)
    context = engine.rootContext()
    context.setContextProperty("monitor", model)
    context.setContextProperty("lineModel", model.line_model)
    context.setContextProperty("factModel", model.fact_model)
    context.setContextProperty("stepModel", model.step_model)
    context.setContextProperty("sendDtmf", SEND_DTMF)
    context.setContextProperty("hangupEnabled", HANGUP_ENABLED)
    context.setContextProperty("showRecordButton", SHOW_RECORD_BUTTON)
    context.setContextProperty("frame", frame)
    engine.load(QUrl.fromLocalFile(str(qml)))
    model._engine = engine
    engine._frame = frame
    roots = engine.rootObjects()
    window = roots[0] if roots else None
    if window is not None:
        _fit_call_window(window, frame)

        def present() -> None:
            present_call_window(window)

        model._present_window = present
        model.callStarted.connect(present)
    return window


def should_present_call(seen_id: str, call_id: str, state: str, *, auto_show: bool) -> bool:
    if not auto_show:
        return False
    if state in {"queued", "dialing"}:
        return True
    return bool(call_id) and call_id != seen_id


def _call_window_is_open(window) -> bool:
    from PySide6.QtGui import QWindow

    return window.isVisible() and window.visibility() != QWindow.Visibility.Minimized


def present_call_window(window) -> None:
    if not _call_window_is_open(window):
        window.showNormal()
    window.raise_()
    window.requestActivate()


def _fit_call_window(window, frame) -> None:
    from fernruf.desktop.shape import apply_kwin_blur

    def apply() -> None:
        if USE_OPAQUE_FALLBACK:
            frame.set_opaque(True)
            return
        radius = int(window.property("unitRadius") or 24)
        frame.set_opaque(not apply_kwin_blur(window, radius))

    window.widthChanged.connect(apply)
    window.heightChanged.connect(apply)
    apply()


def _tray(app, window) -> None:
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    pixmap = QPixmap(16, 16)
    pixmap.fill()
    icon = QIcon.fromTheme("call-start")
    tray = QSystemTrayIcon(icon if not icon.isNull() else QIcon(pixmap))
    tray.setToolTip(tr("Fernruf"))
    menu = QMenu()
    menu.addAction(tr("Window"), window.show)
    menu.addAction(tr("Quit"), app.quit)
    tray.setContextMenu(menu)
    tray.show()
    app._fernruf_tray = tray


def _connect(model, url: str, token: str) -> None:
    from PySide6.QtWebSockets import QWebSocket

    socket = QWebSocket()
    model._socket = socket
    model._url = url
    model._token = token

    def on_text(message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        kind = msg.get("type")
        if kind == "lifecycle":
            model.set_error("")
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
        model.set_error("")
        model.send_listen()

    def lost(_err=None) -> None:
        model.set_error(
            tr("The connection to the local monitor was lost. The transcript so far is kept.")
        )

    socket.textMessageReceived.connect(on_text)
    socket.binaryMessageReceived.connect(on_binary)
    socket.connected.connect(opened)
    socket.errorOccurred.connect(lost)
    socket.disconnected.connect(lost)
    model._reconnect = lambda: _open(socket, url, token)
    _open(socket, url, token)


def _open(socket, url: str, token: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtNetwork import QNetworkRequest

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
                self._listening = listen_from_disk(bool(pref["listen"]))
                self._volume = float(pref["volume"])
                self._muted = bool(pref["muted"])
                self._number = ""
                self._state = "idle"
                self._elapsed = "00:00"
                self._summary = ""
                self._callee = ""
                self._campaign = ""
                self._error = ""
                self._details = False
                self._example = False
                self._ask_dismissed = False
                self._started_at: float | None = None
                self._timing = False
                self._seen_call = ""
                self.line_model = make_line_model(self)
                self.fact_model = QStringListModel(self)
                self.step_model = QStringListModel(self)
                self._sinks: dict[str, object] = {}
                self._pacers: dict[str, PlaybackPacer] = {}
                self._unsent: dict[str, bytes] = {}
                self._socket = None
                self._reconnect = None
                self._clock = QTimer(self)
                self._clock.setInterval(200)
                self._clock.timeout.connect(self._tick)
                self._clock.start()
                self._play = QTimer(self)
                self._play.setInterval(20)
                self._play.timeout.connect(self._drain_audio)
                self._play.start()

            def _store(self) -> None:
                save_listen_pref(
                    self._path,
                    listen=listen_to_store(self._listening),
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

            @Property(str, notify=changed)
            def calleeName(self) -> str:
                return self._callee

            @Property(str, notify=changed)
            def campaign(self) -> str:
                return self._campaign

            @Property(str, notify=changed)
            def errorMessage(self) -> str:
                return self._error

            @Property(bool, notify=changed)
            def detailsOpen(self) -> bool:
                return self._details

            @Property(bool, notify=changed)
            def exampleData(self) -> bool:
                return self._example

            @Property(bool, notify=changed)
            def askDismissed(self) -> bool:
                return self._ask_dismissed

            @Property(bool, notify=changed)
            def listening(self) -> bool:
                return self._listening

            @Property(float, notify=changed)
            def volume(self) -> float:
                return self._volume

            @Property(bool, notify=changed)
            def muted(self) -> bool:
                return self._muted

            @Property(bool, notify=changed)
            def hasFacts(self) -> bool:
                return self.fact_model.rowCount() > 0

            @Property(bool, notify=changed)
            def hasSteps(self) -> bool:
                return self.step_model.rowCount() > 0

            @Property(bool, notify=changed)
            def hasSummaryCard(self) -> bool:
                return self.hasFacts or self.hasSteps

            @Slot(bool)
            def setListening(self, enabled: bool) -> None:
                self._listening = bool(enabled)
                if self._listening:
                    self._ask_dismissed = True
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

            @Slot()
            def dismissAsk(self) -> None:
                self._ask_dismissed = True
                self.changed.emit()

            @Slot()
            def retryConnection(self) -> None:
                self._details = False
                self._error = ""
                self.changed.emit()
                reconnect = self._reconnect
                if reconnect is not None:
                    reconnect()

            @Slot()
            def toggleDetails(self) -> None:
                self._details = not self._details
                self.changed.emit()

            def set_error(self, message: str) -> None:
                if message == self._error:
                    return
                self._error = message
                if not message:
                    self._details = False
                self.changed.emit()

            def set_state(self, state: str) -> None:
                self._state = state
                self.changed.emit()

            def apply_lifecycle(self, msg: dict) -> None:
                self._number = str(msg.get("to_masked") or "")
                state = str(msg.get("state") or "")
                call_id = str(msg.get("call_id") or "")
                present = should_present_call(
                    self._seen_call, call_id, state, auto_show=monitor_auto_show()
                )
                if call_id:
                    self._seen_call = call_id
                self._state = state
                if state == "dialing":
                    self.line_model.clear()
                    self.fact_model.setStringList([])
                    self.step_model.setStringList([])
                    self._pacers.clear()
                    self._unsent.clear()
                    self._summary = ""
                    self._callee = ""
                    self._campaign = ""
                    self._started_at = time.monotonic()
                    self._timing = True
                    self._elapsed = "00:00"
                    self._ask_dismissed = False
                    self._listening = listen_from_disk(bool(load_listen_pref(self._path)["listen"]))
                    self.send_listen()
                    if not self._listening:
                        self._close_sinks()
                elif state in ("ended", "finalized"):
                    self._timing = False
                    self._tick()
                summary = msg.get("summary")
                if isinstance(summary, dict):
                    self._apply_summary(summary)
                self.changed.emit()
                if present:
                    self.callStarted.emit()

            def _apply_summary(self, summary: dict) -> None:
                self._summary = str(summary.get("one_line") or summary.get("outcome") or "")
                facts = summary.get("key_facts")
                steps = summary.get("follow_ups")
                self.fact_model.setStringList(
                    [str(item) for item in facts if str(item).strip()]
                    if isinstance(facts, list)
                    else []
                )
                self.step_model.setStringList(
                    [str(item) for item in steps if str(item).strip()]
                    if isinstance(steps, list)
                    else []
                )

            def apply_transcript(self, msg: dict) -> None:
                speaker = str(msg.get("speaker") or "")
                text = str(msg.get("text") or "").strip()
                if speaker not in ("agent", "callee") or not text:
                    return
                final = bool(msg.get("final", True))
                clock = time.strftime("%H:%M:%S")
                self.line_model.add(speaker, text, final=final, clock=clock)

            def show_example(self) -> None:
                self._example = True
                self.apply_lifecycle(
                    {"state": "dialing", "to_masked": "+49 30 0000 0000", "summary": None}
                )
                self._callee = "Max Mustermann"
                self._campaign = "Example campaign"
                self._state = "answered"
                self._elapsed = "00:42"
                self._timing = False
                self.setListening(True)
                self.setVolume(0.7)
                self.setMuted(False)
                samples = [
                    (
                        "agent",
                        "Hello, this is an example assistant. Am I speaking with Max Mustermann?",
                        "14:02:05",
                        True,
                    ),
                    ("callee", "Yes. What is this about?", "14:02:11", True),
                    (
                        "agent",
                        "This is a sample call about an appointment on Tuesday at 9:30. Does that still work?",
                        "14:02:14",
                        True,
                    ),
                    (
                        "callee",
                        "Tuesday morning is difficult. Is the afternoon open?",
                        "14:02:22",
                        True,
                    ),
                    (
                        "agent",
                        "Tuesday at 15:00 is free. Should the appointment move?",
                        "14:02:27",
                        True,
                    ),
                    ("callee", "Yes, 15:00 works.", "provisional", False),
                ]
                for speaker, text, clock, final in samples:
                    self.line_model.add(speaker, text, final=final, clock=clock)
                self.changed.emit()

            def play(self, frame: dict) -> None:
                if not self._listening or self._muted:
                    return
                pcm = frame.get("pcm") or b""
                if not pcm:
                    return
                direction = str(frame.get("direction") or "")
                pacer = self._pacers.get(direction)
                if pacer is None:
                    pacer = PlaybackPacer()
                    self._pacers[direction] = pacer
                pacer.push(
                    pcm,
                    sample_rate=int(frame.get("sample_rate") or MONITOR_RATE),
                    channels=int(frame.get("channels") or 1),
                )

            def _drain_audio(self) -> None:
                if not self._listening or self._muted:
                    return
                for direction, pacer in self._pacers.items():
                    chunks = pacer.take()
                    pending = self._unsent.get(direction) or b""
                    if pending:
                        chunks.insert(0, pending)
                        self._unsent[direction] = b""
                    if not chunks:
                        continue
                    opened = self._sink(direction)
                    if opened is None:
                        continue
                    self._write_pcm(direction, opened[0], b"".join(chunks))

            def _write_pcm(self, direction: str, device, pcm: bytes) -> None:
                view = pcm
                while view:
                    wrote = device.write(view)
                    if wrote is None or wrote < 0:
                        return
                    if wrote == 0:
                        self._unsent[direction] = bytes(view)
                        return
                    view = view[wrote:]

            def _sink(self, direction: str):
                from PySide6.QtMultimedia import QAudioFormat, QAudioSink

                current = self._sinks.get(direction)
                if current is not None and current[0] == MONITOR_RATE:
                    return current[1:]
                fmt = QAudioFormat()
                fmt.setSampleRate(MONITOR_RATE)
                fmt.setChannelCount(1)
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                audio = QAudioSink(fmt)
                audio.setVolume(0.0 if self._muted else self._volume)
                device = audio.start()
                if device is None:
                    return None
                self._sinks[direction] = (MONITOR_RATE, device, audio)
                return device, audio

            def _apply_volume(self) -> None:
                level = 0.0 if self._muted else self._volume
                for _rate, _device, audio in self._sinks.values():
                    audio.setVolume(level)

            def _close_sinks(self) -> None:
                for _rate, _device, audio in self._sinks.values():
                    audio.stop()
                self._sinks.clear()
                self._pacers.clear()
                self._unsent.clear()

            def _tick(self) -> None:
                if not self._timing or self._started_at is None:
                    return
                seconds = max(0, int(time.monotonic() - self._started_at))
                text = f"{seconds // 60:02d}:{seconds % 60:02d}"
                if text != self._elapsed:
                    self._elapsed = text
                    self.changed.emit()

        _Model.__name__ = "CallModel"
        return _Model(pref_path)
