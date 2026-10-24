from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallEvent:
    call_id: str
    kind: str
    at_ms: int
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = {
            "call_id": self.call_id,
            "kind": self.kind,
            "at_ms": self.at_ms,
            "data": self.data,
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class CallEventBus:
    """In-process pubsub for M5 live view."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[CallEvent]]] = {}
        self._history: dict[str, list[CallEvent]] = {}

    def subscribe(self, call_id: str) -> asyncio.Queue[CallEvent]:
        q: asyncio.Queue[CallEvent] = asyncio.Queue()
        self._queues.setdefault(call_id, []).append(q)
        return q

    def emit(self, call_id: str, kind: str, **data: Any) -> CallEvent:
        ev = CallEvent(call_id=call_id, kind=kind, at_ms=int(time.time() * 1000), data=data)
        self._history.setdefault(call_id, []).append(ev)
        for q in self._queues.get(call_id, []):
            q.put_nowait(ev)
        return ev

    def history(self, call_id: str) -> list[CallEvent]:
        return list(self._history.get(call_id, []))


GLOBAL_EVENT_BUS = CallEventBus()
