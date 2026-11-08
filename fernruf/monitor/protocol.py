"""Messages on the local monitor socket.

Text frames are JSON objects with ``v`` 1.

* ``{"v":1,"type":"auth","token":"..."}`` client to server, when the
  handshake has no Authorization header.
* ``{"v":1,"type":"auth_ok"}`` server to client.
* ``{"v":1,"type":"listen","enabled":true}`` client to server. Audio stays
  off until this is true. The server answers with the same shape.
* ``{"v":1,"type":"lifecycle","call_id","state","at","to_masked","outcome","summary"}``
* ``{"v":1,"type":"transcript","call_id","speaker","text","t_ms","final"}``
  ``speaker`` is ``agent`` or ``callee``. ``final`` is false only for an
  interim hypothesis. This pipeline currently emits finals.

Binary frames are little-endian::

    magic "FR1" (3) | direction u8 | sample_rate u32 | channels u16
    | pts_ms u32 | call_id_len u8 | call_id | pcm s16le

``direction`` 1 is the callee (inbound). 2 is the bot TTS (outbound).
"""

from __future__ import annotations

import struct
from typing import Any

from fernruf.monitor.state import CallLifecycle

_HEADER = struct.Struct("<3sBIHIB")
_DIRECTIONS = {1: "inbound", 2: "outbound"}


def mask_e164(number: str) -> str:
    """Keep the last four digits. Everything else becomes a bullet."""
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        return "••••"
    hidden = len(digits) - 4
    return ("•" * hidden) + digits[-4:]


def lifecycle_message(
    *,
    call_id: str,
    state: CallLifecycle,
    at: str,
    to_masked: str,
    outcome: str | None,
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "lifecycle",
        "call_id": call_id,
        "state": state.value,
        "at": at,
        "to_masked": to_masked,
        "outcome": outcome,
        "summary": summary,
    }


def transcript_message(
    *,
    call_id: str,
    speaker: str,
    text: str,
    t_ms: int,
    final: bool,
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "transcript",
        "call_id": call_id,
        "speaker": speaker,
        "text": text,
        "t_ms": int(t_ms),
        "final": bool(final),
    }


def pack_audio(
    *,
    call_id: str,
    direction: str,
    pcm: bytes,
    sample_rate: int,
    channels: int,
    pts_ms: int,
) -> bytes:
    if direction not in ("inbound", "outbound"):
        raise ValueError("direction must be inbound or outbound")
    cid = call_id.encode("ascii")[:32]
    code = 1 if direction == "inbound" else 2
    header = _HEADER.pack(
        b"FR1",
        code,
        int(sample_rate) & 0xFFFFFFFF,
        int(channels) & 0xFFFF,
        int(pts_ms) & 0xFFFFFFFF,
        len(cid),
    )
    return header + cid + pcm


def unpack_audio(blob: bytes) -> dict[str, Any]:
    if len(blob) < _HEADER.size:
        raise ValueError("short audio frame")
    magic, code, sample_rate, channels, pts_ms, n = _HEADER.unpack_from(blob)
    if magic != b"FR1" or code not in _DIRECTIONS:
        raise ValueError("bad audio frame")
    start = _HEADER.size
    end = start + n
    if end > len(blob):
        raise ValueError("short audio frame")
    return {
        "direction": _DIRECTIONS[code],
        "sample_rate": sample_rate,
        "channels": channels,
        "pts_ms": pts_ms,
        "call_id": blob[start:end].decode("ascii"),
        "pcm": blob[end:],
    }
