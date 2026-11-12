from __future__ import annotations

import contextvars
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from fernruf.debug.redact import redact
from fernruf.monitor.protocol import mask_e164

_current_call: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fernruf_debug_call", default=""
)

_MAX_EVENTS = 500


class DebugTrace:
    def __init__(
        self,
        *,
        enabled: bool = False,
        persist: bool = False,
        persist_path: str | Path | None = None,
        clock=None,
    ) -> None:
        self.enabled = bool(enabled)
        self.persist = bool(persist) and self.enabled
        self.persist_path = Path(persist_path) if persist_path else None
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._next = 1
        self._calls: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._listeners: list[queue.Queue] = []

    def bind(self, call_id: str, *, to: str = "") -> None:
        if not self.enabled or not call_id:
            return
        with self._lock:
            self._ensure(call_id, to)

    def note(
        self,
        *,
        kind: str,
        name: str,
        status: str = "ok",
        duration_ms: float | None = None,
        detail: dict[str, Any] | None = None,
        call_id: str = "",
        to: str = "",
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        chosen = call_id or _current_call.get() or "mcp"
        event = {
            "id": 0,
            "call_id": chosen,
            "kind": kind,
            "name": name,
            "at_ms": int(self._clock() * 1000),
            "duration_ms": None if duration_ms is None else round(float(duration_ms), 1),
            "status": status,
            "detail": redact(detail or {}),
        }
        with self._lock:
            event["id"] = self._next
            self._next += 1
            call = self._ensure(chosen, to)
            call["events"].append(event)
            self._trim()
            listeners = list(self._listeners)
            if self.persist and self.persist_path is not None:
                self._write(event)
        for listener in listeners:
            try:
                listener.put_nowait(event)
            except queue.Full:
                continue
        return event

    def listen(self) -> queue.Queue:
        listener: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._listeners.append(listener)
        return listener

    def unlisten(self, listener: queue.Queue) -> None:
        with self._lock:
            self._listeners = [item for item in self._listeners if item is not listener]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = []
            for call_id in self._order:
                call = self._calls[call_id]
                calls.append(
                    {
                        "call_id": call["call_id"],
                        "to_masked": call["to_masked"],
                        "started_ms": call["started_ms"],
                        "events": list(call["events"]),
                    }
                )
        return {"calls": calls}

    def _ensure(self, call_id: str, to: str) -> dict[str, Any]:
        call = self._calls.get(call_id)
        masked = mask_e164(to) if to else ""
        if call is None:
            call = {
                "call_id": call_id,
                "to_masked": masked,
                "started_ms": int(self._clock() * 1000),
                "events": [],
            }
            self._calls[call_id] = call
            self._order.append(call_id)
        elif masked and not call["to_masked"]:
            call["to_masked"] = masked
        return call

    def _trim(self) -> None:
        total = sum(len(call["events"]) for call in self._calls.values())
        while total > _MAX_EVENTS and self._order:
            oldest = self._calls[self._order[0]]
            if oldest["events"]:
                oldest["events"].pop(0)
                total -= 1
                continue
            self._order.pop(0)
            self._calls.pop(oldest["call_id"], None)

    def _write(self, event: dict[str, Any]) -> None:
        path = self.persist_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


_trace = DebugTrace(enabled=False)


def configure_trace(
    *,
    enabled: bool,
    persist: bool = False,
    persist_path: str | Path | None = None,
) -> DebugTrace:
    global _trace
    _trace = DebugTrace(enabled=enabled, persist=persist, persist_path=persist_path)
    return _trace


def current_trace() -> DebugTrace:
    return _trace


def note(**kwargs: Any) -> dict[str, Any] | None:
    return _trace.note(**kwargs)


def bind_call(call_id: str, *, to: str = "") -> contextvars.Token:
    _trace.bind(call_id, to=to)
    return _current_call.set(call_id)


def reset_call(token: contextvars.Token) -> None:
    _current_call.reset(token)
