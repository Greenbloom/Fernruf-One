"""Switches for the call window. Flip one of these to change a default.

* No DTMF from this window.
* Listening is chosen again on every call and is not written to disk.
* The window opens when a call starts, and the tray icon stays.
* KWin blurs the rounded window when the session is X11. USE_OPAQUE_FALLBACK keeps the dark plate.
* Hang-up stays off. Recording stays off.
"""

from __future__ import annotations

import os

SEND_DTMF = False
PERSIST_LISTEN = False
RAISE_WINDOW_ON_CALL = True
_AUTO_SHOW_OFF = frozenset({"0", "false", "no", "off"})
SHOW_TRAY_ICON = True
USE_OPAQUE_FALLBACK = False
HANGUP_ENABLED = False
SHOW_RECORD_BUTTON = False


def monitor_auto_show() -> bool:
    raw = os.environ.get("FERNRUF_MONITOR_AUTO_SHOW")
    if raw is None or not raw.strip():
        return RAISE_WINDOW_ON_CALL
    return raw.strip().lower() not in _AUTO_SHOW_OFF


def listen_to_store(enabled: bool) -> bool:
    """Value written for ``listen``. Off means a yes lasts only for this call."""
    if not PERSIST_LISTEN:
        return False
    return bool(enabled)


def listen_from_disk(saved: bool) -> bool:
    """Value used when a call starts. Off ignores a saved yes."""
    if not PERSIST_LISTEN:
        return False
    return bool(saved)
