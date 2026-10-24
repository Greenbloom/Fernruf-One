from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fernruf.policy import PolicyDecision


_SECRET_KEYS = frozenset(
    {
        "token",
        "authorization",
        "password",
        "secret",
        "api_key",
        "mcp_token",
    }
)


def append_audit_event(path: Path, event: dict) -> None:
    """Append one JSONL audit record. Drops secret-looking keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(tz=timezone.utc).isoformat()}
    for key, value in event.items():
        if key.lower() in _SECRET_KEYS:
            continue
        record[key] = value
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_call_audit(
    path: Path,
    *,
    to_e164: str,
    decision: PolicyDecision,
    quiet_hours: str,
    extra: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(tz=timezone.utc).isoformat(),
        "to": to_e164,
        "policy_outcome": decision.outcome.value,
        "policy_reason": decision.reason,
        "quiet_hours": quiet_hours,
    }
    if extra:
        record.update(extra)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
