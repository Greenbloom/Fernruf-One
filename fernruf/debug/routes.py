from __future__ import annotations

import asyncio
import queue
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from fernruf.debug.trace import configure_trace, current_trace
from fernruf.monitor.server import monitor_bearer
from fernruf.service.security import bearer_matches

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_PAGE = Path(__file__).with_name("page.html")


def _peer(host: str | None) -> bool:
    return (host or "") in _LOOPBACK


def install_debug_routes(app: FastAPI, settings) -> None:
    enabled = bool(getattr(settings, "debug_ui_enabled", False))
    persist = bool(getattr(settings, "debug_ui_persist", False))
    path = getattr(settings, "debug_ui_path", "") or ""
    configure_trace(
        enabled=enabled,
        persist=persist,
        persist_path=path or ("data/debug-ui.jsonl" if persist else None),
    )
    token = monitor_bearer(settings)
    page = _PAGE.read_text(encoding="utf-8")

    def refused(host: str | None, header: str | None = None) -> JSONResponse | None:
        if not enabled or not token:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not _peer(host):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if header is None:
            return None
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not presented or not bearer_matches(presented, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    @app.get("/debug", response_model=None)
    @app.get("/debug/", response_model=None)
    async def debug_page(request: Request):
        host = request.client.host if request.client else ""
        denied = refused(host)
        if denied is not None:
            return denied
        return HTMLResponse(page)

    @app.get("/debug/calls", response_model=None)
    async def debug_calls(request: Request):
        host = request.client.host if request.client else ""
        denied = refused(host, request.headers.get("authorization", ""))
        if denied is not None:
            return denied
        return JSONResponse(current_trace().snapshot())

    @app.websocket("/debug/live")
    async def debug_live(websocket: WebSocket) -> None:
        host = websocket.client.host if websocket.client else ""
        if not enabled or not token or not _peer(host):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
        except Exception:
            await websocket.close(code=1008)
            return
        presented = str(raw.get("token") or "") if raw.get("type") == "auth" else ""
        if not presented or not bearer_matches(presented, token):
            await websocket.close(code=1008)
            return
        trace = current_trace()
        await websocket.send_json({"type": "snapshot", **trace.snapshot()})
        listener = trace.listen()
        try:
            while True:
                event = await asyncio.to_thread(listener.get)
                if not event:
                    continue
                await websocket.send_json({"type": "event", "event": event})
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            trace.unlisten(listener)
            try:
                listener.put_nowait(None)
            except queue.Full:
                pass
