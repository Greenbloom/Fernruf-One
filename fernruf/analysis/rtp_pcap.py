from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

DLT_EN10MB = 1
DLT_LINUX_SLL = 113
DLT_LINUX_SLL2 = 276

PCAP_MAGIC = 0xA1B2C3D4
PCAP_MAGIC_LE = 0xD4C3B2A1


@dataclass
class RtpStats:
    udp_packets: int = 0
    rtp_packets: int = 0
    pcma_count: int = 0
    telephone_event_count: int = 0
    dtmf_events: list[int] = field(default_factory=list)


def _ip4_udp(raw: bytes, offset: int) -> tuple[int, int, bytes] | None:
    if offset + 20 > len(raw):
        return None
    ver = raw[offset] >> 4
    if ver != 4:
        return None
    ihl = (raw[offset] & 0x0F) * 4
    if offset + ihl + 8 > len(raw):
        return None
    proto = raw[offset + 9]
    if proto != 17:
        return None
    udp_off = offset + ihl
    src_port, dst_port = struct.unpack_from("!HH", raw, udp_off)
    udp_len = struct.unpack_from("!H", raw, udp_off + 4)[0]
    if udp_off + udp_len > len(raw):
        udp_len = len(raw) - udp_off
    if udp_len < 8:
        return None
    payload = raw[udp_off + 8 : udp_off + udp_len]
    return src_port, dst_port, payload


def _ip4_udp_payload(raw: bytes, offset: int) -> bytes | None:
    parsed = _ip4_udp(raw, offset)
    if parsed is None:
        return None
    _src, _dst, payload = parsed
    return payload


def _link_udp(raw: bytes, linktype: int) -> tuple[int, int, bytes] | None:
    if linktype == DLT_EN10MB:
        if len(raw) < 14:
            return None
        eth_type = struct.unpack_from("!H", raw, 12)[0]
        if eth_type != 0x0800:
            return None
        return _ip4_udp(raw, 14)
    if linktype == DLT_LINUX_SLL:
        if len(raw) < 16:
            return None
        proto = struct.unpack_from("!H", raw, 14)[0]
        if proto != 0x0800:
            return None
        return _ip4_udp(raw, 16)
    if linktype == DLT_LINUX_SLL2:
        if len(raw) < 20:
            return None
        proto = struct.unpack_from("!H", raw, 0)[0]
        if proto != 0x0800:
            return None
        return _ip4_udp(raw, 20)
    return None


def _link_payload(raw: bytes, linktype: int) -> bytes | None:
    parsed = _link_udp(raw, linktype)
    if parsed is None:
        return None
    _src, _dst, payload = parsed
    return payload


def _parse_rtp(payload: bytes, stats: RtpStats) -> None:
    if len(payload) < 12:
        return
    b0, b1 = payload[0], payload[1]
    if (b0 >> 6) != 2:
        return
    cc = b0 & 0x0F
    pt = b1 & 0x7F
    hdr_len = 12 + cc * 4
    if len(payload) < hdr_len:
        return
    stats.rtp_packets += 1
    body = payload[hdr_len:]
    if pt == 8:
        stats.pcma_count += 1
    elif pt == 101 and len(body) >= 4:
        stats.telephone_event_count += 1
        stats.dtmf_events.append(body[0])


def analyze_pcap(path: Path, *, udp_port: int | None = None) -> RtpStats:
    data = path.read_bytes()
    stats = RtpStats()
    if len(data) < 24:
        return stats
    magic_be = struct.pack(">I", PCAP_MAGIC)
    magic_le = struct.pack("<I", PCAP_MAGIC)
    if data[:4] == magic_be:
        endian = ">"
    elif data[:4] == magic_le:
        endian = "<"
    else:
        return stats
    _ver_major, _ver_minor, _thiszone, _sigfigs, _snaplen, linktype = struct.unpack_from(
        endian + "HHIIII", data, 4
    )
    off = 24
    while off + 16 <= len(data):
        _ts_sec, _ts_usec, incl_len, _orig_len = struct.unpack_from(endian + "IIII", data, off)
        off += 16
        pkt = data[off : off + incl_len]
        off += incl_len
        udp_info = _link_udp(pkt, linktype)
        if udp_info is None:
            continue
        src_port, dst_port, udp = udp_info
        if udp_port is not None and src_port != udp_port and dst_port != udp_port:
            continue
        stats.udp_packets += 1
        _parse_rtp(udp, stats)
    return stats


def format_report(stats: RtpStats) -> str:
    lines = [
        f"UDP_RTP_CANDIDATES={stats.udp_packets}",
        f"RTP_PACKETS={stats.rtp_packets}",
        f"PCMA_COUNT={stats.pcma_count}",
        f"RFC2833_EVENTS={stats.telephone_event_count}",
    ]
    if stats.dtmf_events:
        digits = []
        for ev in stats.dtmf_events:
            if 0 <= ev <= 9:
                digits.append(str(ev))
            elif ev == 10:
                digits.append("*")
            elif ev == 11:
                digits.append("#")
        lines.append(f"RFC2833_DIGITS={''.join(digits) if digits else 'events-only'}")
    else:
        lines.append("RFC2833_DIGITS=")
    pcma_ok = stats.pcma_count >= 50
    lines.append(f"PCMA_PAYLOADS={'ok' if pcma_ok else 'fail'}")
    dtmf_ok = any(ev in (1, 2, 3, 4) for ev in stats.dtmf_events) or stats.telephone_event_count >= 1
    lines.append(f"RFC2833_DTMF={'ok' if dtmf_ok else 'fail'}")
    return "\n".join(lines)
