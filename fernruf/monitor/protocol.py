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

# "country_and_last4" shows the calling code and the last four digits.
# "last4" keeps only those four digits. The window never receives the rest.
NUMBER_MASK = "country_and_last4"

_CALLING_CODES = frozenset(
    {
        "1", "7",
        "20", "27", "30", "31", "32", "33", "34", "36", "39",
        "40", "41", "43", "44", "45", "46", "47", "48", "49",
        "51", "52", "53", "54", "55", "56", "57", "58",
        "60", "61", "62", "63", "64", "65", "66",
        "81", "82", "84", "86", "90", "91", "92", "93", "94", "95", "98",
        "211", "212", "213", "216", "218",
        "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",
        "230", "231", "232", "233", "234", "235", "236", "237", "238", "239",
        "240", "241", "242", "243", "244", "245", "246", "247", "248", "249",
        "250", "251", "252", "253", "254", "255", "256", "257", "258",
        "260", "261", "262", "263", "264", "265", "266", "267", "268", "269",
        "290", "291", "297", "298", "299",
        "350", "351", "352", "353", "354", "355", "356", "357", "358", "359",
        "370", "371", "372", "373", "374", "375", "376", "377", "378", "380",
        "381", "382", "383", "385", "386", "387", "389",
        "420", "421", "423",
        "500", "501", "502", "503", "504", "505", "506", "507", "508", "509",
        "590", "591", "592", "593", "594", "595", "596", "597", "598", "599",
        "670", "672", "673", "674", "675", "676", "677", "678", "679",
        "680", "681", "682", "683", "685", "686", "687", "688", "689",
        "690", "691", "692",
        "850", "852", "853", "855", "856", "880", "886",
        "960", "961", "962", "963", "964", "965", "966", "967", "968",
        "970", "971", "972", "973", "974", "975", "976", "977",
        "992", "993", "994", "995", "996", "998",
    }
)


def _calling_code(digits: str) -> str:
    for length in (3, 2, 1):
        if len(digits) > length + 4 and digits[:length] in _CALLING_CODES:
            return digits[:length]
    return ""


def _bullet_groups(count: int) -> str:
    parts: list[str] = []
    while count > 0:
        take = 3 if count >= 3 else count
        parts.append("•" * take)
        count -= take
    return " ".join(reversed(parts))


def mask_e164(number: str) -> str:
    """Hide a phone number before it reaches the window.

    ``country_and_last4`` keeps the calling code and the last four digits.
    A national number with no calling code keeps only those four digits.
    """
    raw = number.strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 4:
        return "••••"
    last = digits[-4:]
    if NUMBER_MASK != "country_and_last4":
        return ("•" * (len(digits) - 4)) + last
    international = raw.startswith("+")
    code = _calling_code(digits) if international else ""
    hidden = len(digits) - len(code) - 4
    grouped = _bullet_groups(hidden)
    if code:
        return f"+{code} {grouped} {last}".replace("  ", " ")
    if grouped:
        return f"{grouped} {last}"
    return last


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
