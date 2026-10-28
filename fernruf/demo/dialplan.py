"""Asterisk dialplan for the local demo extension.

The extension id is ``600-<8 hex>``. ``${EXTEN:4}`` drops the ``600-`` prefix
and is the MixMonitor filename. This snippet is concatenated after envsubst
so that Asterisk variable is left intact.
"""

from __future__ import annotations

import re

DEMO_CONTEXT = "fernruf-demo"
DEMO_EXTEN_PREFIX = "600-"
CALLEE_WS_CLIENT = "fernruf-callee"
CALLEE_WS_DIAL = f"WebSocket/{CALLEE_WS_CLIENT}/c(slin16)f(json)d(both)"
MONITOR_DIR = "/var/spool/asterisk/monitor"
_CALL_ID = re.compile(r"[0-9a-f]{8}")


def demo_extensions_conf() -> str:
    offset = len(DEMO_EXTEN_PREFIX)
    return (
        f"[{DEMO_CONTEXT}]\n"
        "; Local AI-to-AI demo. This context never dials the SIP trunk.\n"
        f"exten => _{DEMO_EXTEN_PREFIX}.,1,NoOp(Fernruf demo callee ${{EXTEN}})\n"
        " same => n,Answer()\n"
        f" same => n,MixMonitor({MONITOR_DIR}/${{EXTEN:{offset}}}.wav)\n"
        f" same => n,Dial({CALLEE_WS_DIAL})\n"
        " same => n,Hangup()\n"
    )


def demo_endpoint(call_id: str) -> str:
    if not _CALL_ID.fullmatch(call_id):
        raise ValueError("demo call id must be 8 lowercase hex characters")
    # /n keeps both Local halves so MixMonitor on the dialplan leg hears both voices.
    return f"Local/{DEMO_EXTEN_PREFIX}{call_id}@{DEMO_CONTEXT}/n"


def monitor_id_from_endpoint(endpoint: str) -> str:
    exten = endpoint.split("@", 1)[0].removeprefix("Local/")
    return exten[len(DEMO_EXTEN_PREFIX) :]
