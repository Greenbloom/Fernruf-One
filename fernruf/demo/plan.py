"""Dial target for one local demo call."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fernruf.demo.dialplan import MONITOR_DIR, demo_endpoint, monitor_id_from_endpoint
from fernruf.demo.guard import assert_local_demo_endpoint

HOST_MONITOR_DIR = Path("data/asterisk-monitor")


@dataclass(frozen=True)
class DemoCallPlan:
    call_id: str
    endpoint: str
    out: Path
    monitor_container_path: str
    monitor_host_path: Path


def plan_demo_call(out: Path, call_id: str | None = None) -> DemoCallPlan:
    cid = call_id or uuid.uuid4().hex[:8]
    endpoint = assert_local_demo_endpoint(demo_endpoint(cid))
    monitor_name = f"{monitor_id_from_endpoint(endpoint)}.wav"
    return DemoCallPlan(
        call_id=cid,
        endpoint=endpoint,
        out=out,
        monitor_container_path=f"{MONITOR_DIR}/{monitor_name}",
        monitor_host_path=HOST_MONITOR_DIR / monitor_name,
    )
