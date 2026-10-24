#!/usr/bin/env python3
"""Analyze RTP in a pcap (PCMA + RFC4733). Wrapper around fernruf.analysis.rtp_pcap."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fernruf.analysis.rtp_pcap import analyze_pcap, format_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze RTP PCMA/DTMF in a pcap")
    parser.add_argument("pcap", type=Path)
    parser.add_argument(
        "--udp-port",
        type=int,
        default=None,
        help="Only count UDP packets where src or dst equals this port (e.g. SIPp media)",
    )
    args = parser.parse_args()
    stats = analyze_pcap(args.pcap, udp_port=args.udp_port)
    report = format_report(stats)
    if args.udp_port is not None:
        report = f"FILTER_UDP_PORT={args.udp_port}\n{report}"
    print(report)
    pcma_ok = stats.pcma_count >= 50
    raise SystemExit(0 if pcma_ok else 1)


if __name__ == "__main__":
    main()
