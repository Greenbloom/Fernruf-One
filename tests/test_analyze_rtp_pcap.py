import struct
from pathlib import Path

from fernruf.analysis.rtp_pcap import DLT_LINUX_SLL, analyze_pcap


def _build_rtp_sll_packet(pt: int, body: bytes) -> bytes:
    hdr = bytes([0x80, pt & 0x7F, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    rtp = hdr + body
    udp_len = 8 + len(rtp)
    udp = struct.pack("!HHHH", 5004, 10000, udp_len, 0) + rtp
    ip_len = 20 + len(udp)
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        ip_len,
        0x1234,
        0,
        64,
        17,
        0,
        struct.pack("!I", 0x7F000001),
        struct.pack("!I", 0x7F000001),
    )
    sll = struct.pack("!HHH", 0, 1, 0) + b"\x00" * 8 + struct.pack("!H", 0x0800) + ip + udp
    return sll


def _write_pcap(path: Path, packets: list[bytes]) -> None:
    gh = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, DLT_LINUX_SLL)
    body = b""
    for pkt in packets:
        body += struct.pack("<IIII", 0, 0, len(pkt), len(pkt)) + pkt
    path.write_bytes(gh + body)


def test_analyze_pcma_and_rfc2833(tmp_path: Path) -> None:
    pkts = [_build_rtp_sll_packet(8, b"\xff" * 160) for _ in range(60)]
    pkts.append(_build_rtp_sll_packet(101, bytes([1, 0x80, 10, 0])))
    pcap = tmp_path / "t.pcap"
    _write_pcap(pcap, pkts)
    stats = analyze_pcap(pcap)
    assert stats.pcma_count >= 50
    assert stats.telephone_event_count >= 1
    assert 1 in stats.dtmf_events
