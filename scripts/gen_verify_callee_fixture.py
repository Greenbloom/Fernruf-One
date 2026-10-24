#!/usr/bin/env python3
"""Generate loopback callee PCMA fixtures for SIPp (raw alaw + optional RTP pcap)."""
from __future__ import annotations

import struct
from pathlib import Path

from fernruf.tts.audio import _pcm_to_alaw, _fixture_pcm
from fernruf.tts.rest_stream import pcm_to_wav

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "scripts/verify/fixtures"
FISH_FIXTURE_DIR = ROOT / "tests/fixtures"
FISH_WAV = FISH_FIXTURE_DIR / "fish_tts_de.wav"
STT_WAV = FISH_FIXTURE_DIR / "stt_smoke_de.wav"
OUT_ALAW = FIXTURE_DIR / "callee-hallo.alaw"
OUT_PCAP = FIXTURE_DIR / "callee-hallo-rtp.pcap"
CALLEE_TEXT = "Hallo, ich höre Sie gut."

PCAP_MAGIC_LE = 0xA1B2C3D4
DLT_EN10MB = 1
RTP_PT_PCMA = 8
SSRC = 0xCA11B07


def _build_rtp_pcap(alaw: bytes, *, src_ip: str = "127.0.0.1", dst_ip: str = "127.0.0.1") -> bytes:
    """Minimal Ethernet/IPv4/UDP/RTP pcap for SIPp play_pcap_audio."""
    src = bytes(int(x) for x in src_ip.split("."))
    dst = bytes(int(x) for x in dst_ip.split("."))
    src_port = 6002
    dst_port = 10000
    seq = 0
    ts = 0
    chunks = [alaw[i : i + 160] for i in range(0, len(alaw), 160)]
    packets: list[bytes] = []
    for chunk in chunks:
        if len(chunk) < 160:
            chunk = chunk.ljust(160, b"\xd5")
        rtp_hdr = struct.pack(
            "!BBHII",
            0x80,
            RTP_PT_PCMA,
            seq & 0xFFFF,
            ts & 0xFFFFFFFF,
            SSRC,
        )
        seq += 1
        ts += 160
        udp_payload = rtp_hdr + chunk
        udp_len = 8 + len(udp_payload)
        ip_len = 20 + udp_len
        ip_hdr = struct.pack(
            "!BBHHHBBH4s4s",
            0x45,
            0,
            ip_len,
            0,
            0,
            64,
            17,
            0,
            src,
            dst,
        )
        udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
        eth = b"\x00" * 6 + b"\x00" * 6 + struct.pack("!H", 0x0800)
        packets.append(eth + ip_hdr + udp_hdr + udp_payload)

    global_hdr = struct.pack("<IHHiIII", PCAP_MAGIC_LE, 2, 4, 0, 0, 65535, DLT_EN10MB)
    body = bytearray(global_hdr)
    for pkt in packets:
        body.extend(struct.pack("<IIII", 0, 0, len(pkt), len(pkt)))
        body.extend(pkt)
    return bytes(body)


def main() -> None:
    pcm = _fixture_pcm(CALLEE_TEXT, sample_rate=8000, duration_s=2.5)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    alaw = _pcm_to_alaw(pcm, 8000)
    OUT_ALAW.write_bytes(alaw)
    OUT_PCAP.write_bytes(_build_rtp_pcap(alaw))
    FISH_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fish_pcm = _fixture_pcm("Hallo von Fish.", sample_rate=16000, duration_s=1.2)
    FISH_WAV.write_bytes(pcm_to_wav(fish_pcm, 16000))
    stt_pcm = _fixture_pcm("Telefonat auf Deutsch.", sample_rate=16000, duration_s=1.5)
    STT_WAV.write_bytes(pcm_to_wav(stt_pcm, 16000))
    print(f"wrote {OUT_ALAW} ({OUT_ALAW.stat().st_size} bytes)")
    print(f"wrote {OUT_PCAP} ({OUT_PCAP.stat().st_size} bytes)")
    print(f"wrote {FISH_WAV} ({FISH_WAV.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
