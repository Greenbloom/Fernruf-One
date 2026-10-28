"""Fresh clones must be able to write audit and latency files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fernruf.audit import append_audit_event, append_call_audit
from fernruf.data_dir import DATA_DIR_FIX
from fernruf.policy import PolicyDecision, PolicyOutcome
from fernruf.voice.latency import LatencyLog, TurnLatency


def _block(tmp_path: Path) -> Path:
    blocked = tmp_path / "data"
    blocked.mkdir()
    blocked.chmod(0o555)
    if os.access(blocked, os.W_OK):
        pytest.skip("this process can still write the directory")
    return blocked


def test_unwritable_audit_log_names_the_mkdir_fix(tmp_path: Path) -> None:
    path = _block(tmp_path) / "audit.jsonl"
    with pytest.raises(PermissionError, match="mkdir -p data/asterisk-sounds data/asterisk-generated"):
        append_audit_event(path, {"event": "m2_test_call_started"})
    with pytest.raises(PermissionError, match=DATA_DIR_FIX):
        append_call_audit(
            path,
            to_e164="dest",
            decision=PolicyDecision(outcome=PolicyOutcome.ALLOW, reason=None),
            quiet_hours="off",
        )


def test_unwritable_latency_log_names_the_mkdir_fix(tmp_path: Path) -> None:
    path = _block(tmp_path) / "latency.jsonl"
    with pytest.raises(PermissionError, match=DATA_DIR_FIX):
        LatencyLog(path)
    # A directory created after init can still fail on append.
    writable = tmp_path / "later"
    writable.mkdir()
    log = LatencyLog(writable / "latency.jsonl")
    writable.chmod(0o555)
    if os.access(writable, os.W_OK):
        pytest.skip("this process can still write the directory")
    row = TurnLatency(call_id="c", turn=1, llm_provider="p", llm_model="m")
    with pytest.raises(PermissionError, match=DATA_DIR_FIX):
        log.append(row)
