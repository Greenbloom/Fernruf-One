from __future__ import annotations

import ctypes
import ctypes.util
import math

_BLUR_ATOM = "_KDE_NET_WM_BLUR_BEHIND_REGION"
_XCB_PROP_MODE_REPLACE = 0
_XCB_ATOM_CARDINAL = 6


def rounded_blur_rects(width: int, height: int, radius: int) -> list[tuple[int, int, int, int]]:
    width = int(width)
    height = int(height)
    radius = int(radius)
    if width <= 0 or height <= 0:
        return []
    if radius <= 0:
        return [(0, 0, width, height)]
    radius = min(radius, width // 2, height // 2)
    bands: list[tuple[int, int, int, int]] = []
    for row in range(radius):
        inset = _corner_inset(radius, row)
        span = width - 2 * inset
        if span > 0:
            bands.append((inset, row, span, 1))
    middle = height - 2 * radius
    if middle > 0:
        bands.append((0, radius, width, middle))
    for row in range(radius):
        inset = _corner_inset(radius, radius - 1 - row)
        span = width - 2 * inset
        if span > 0:
            bands.append((inset, height - radius + row, span, 1))
    return bands


def _corner_inset(radius: int, row_from_outer_edge: int) -> int:
    center_from_circle = radius - (row_from_outer_edge + 0.5)
    reach = math.sqrt(max(0.0, radius * radius - center_from_circle * center_from_circle))
    return max(0, min(radius, math.ceil(radius - reach - 0.5)))


def apply_kwin_blur(window, radius: int) -> bool:
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return False
    app = QGuiApplication.instance()
    if app is None or app.platformName() != "xcb":
        return False
    try:
        display = int(app.nativeInterface().connection() or 0)
        window_id = int(window.winId() or 0)
    except (AttributeError, TypeError, ValueError):
        return False
    if display == 0 or window_id == 0:
        return False
    try:
        return _set_x11_blur(display, window_id, int(window.width()), int(window.height()), int(radius))
    except (OSError, AttributeError):
        return False


def _set_x11_blur(connection: int, window_id: int, width: int, height: int, radius: int) -> bool:
    bands = rounded_blur_rects(width, height, radius)
    if not bands:
        return False
    library = ctypes.util.find_library("xcb")
    if not library:
        return False
    xcb = ctypes.CDLL(library)

    class _Cookie(ctypes.Structure):
        _fields_ = [("sequence", ctypes.c_uint)]

    class _InternReply(ctypes.Structure):
        _fields_ = [
            ("response_type", ctypes.c_uint8),
            ("pad0", ctypes.c_uint8),
            ("sequence", ctypes.c_uint16),
            ("length", ctypes.c_uint32),
            ("atom", ctypes.c_uint32),
        ]

    xcb.xcb_intern_atom.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_char_p]
    xcb.xcb_intern_atom.restype = _Cookie
    xcb.xcb_intern_atom_reply.argtypes = [ctypes.c_void_p, _Cookie, ctypes.c_void_p]
    xcb.xcb_intern_atom_reply.restype = ctypes.POINTER(_InternReply)
    xcb.xcb_change_property.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint8,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint8,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    xcb.xcb_change_property.restype = _Cookie
    xcb.xcb_flush.argtypes = [ctypes.c_void_p]
    xcb.xcb_flush.restype = ctypes.c_int

    name = _BLUR_ATOM.encode()
    cookie = xcb.xcb_intern_atom(connection, 0, len(name), name)
    reply = xcb.xcb_intern_atom_reply(connection, cookie, None)
    if not reply:
        return False
    atom = int(reply.contents.atom)
    libc = ctypes.CDLL(None)
    libc.free.argtypes = [ctypes.c_void_p]
    libc.free(reply)
    if atom == 0:
        return False

    values = (ctypes.c_uint32 * (len(bands) * 4))()
    for index, (x, y, w, h) in enumerate(bands):
        values[index * 4] = x
        values[index * 4 + 1] = y
        values[index * 4 + 2] = w
        values[index * 4 + 3] = h
    xcb.xcb_change_property(
        connection,
        _XCB_PROP_MODE_REPLACE,
        window_id,
        atom,
        _XCB_ATOM_CARDINAL,
        32,
        len(bands) * 4,
        ctypes.cast(values, ctypes.c_void_p),
    )
    xcb.xcb_flush(connection)
    return True
