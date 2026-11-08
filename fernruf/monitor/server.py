"""WebSocket server bound to loopback. The token never appears in a log line."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from fernruf.monitor.hub import CallMonitor, Subscription
from fernruf.service.security import BindError, bearer_matches

LOG = logging.getLogger("fernruf.monitor")

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_AUTH_TIMEOUT_S = 2.0


def peer_is_loopback(host: str) -> bool:
    return host in _LOOPBACK


@dataclass
class MonitorServer:
    port: int
    _server: object

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()


def monitor_bearer(settings) -> str:
    """Static monitor token, or the MCP token when no separate one is set."""
    chosen = (getattr(settings, "monitor_token", "") or "").strip()
    if chosen:
        return chosen
    return (getattr(settings, "mcp_token", "") or "").strip()


async def start_monitor_server(
    monitor: CallMonitor,
    token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8791,
) -> MonitorServer:
    if host not in _LOOPBACK:
        raise BindError("the call monitor listens on 127.0.0.1 only")
    if not token:
        raise BindError("the call monitor needs a token")

    async def process_request(connection, request):
        peer = connection.remote_address[0] if connection.remote_address else ""
        if not peer_is_loopback(peer):
            return Response(403, "Forbidden", Headers())
        header = request.headers.get("Authorization", "")
        if not header:
            connection.fernruf_authed = False
            return None
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if presented and bearer_matches(presented, token):
            connection.fernruf_authed = True
            return None
        LOG.info("monitor auth failed")
        return Response(401, "Unauthorized", Headers())

    async def handler(connection) -> None:
        peer = connection.remote_address[0] if connection.remote_address else ""
        if not peer_is_loopback(peer):
            await connection.close(code=1008)
            return
        if not getattr(connection, "fernruf_authed", False):
            if not await _auth_message(connection, token):
                return
        sub = monitor.subscribe()
        await connection.send(json.dumps({"v": 1, "type": "auth_ok"}))
        try:
            await _session(connection, monitor, sub)
        finally:
            monitor.unsubscribe(sub)

    server = await serve(
        handler,
        host,
        port,
        process_request=process_request,
        compression=None,
        max_size=4096,
        server_header=None,
    )
    bound = next(iter(server.sockets)).getsockname()[1]
    return MonitorServer(port=bound, _server=server)


async def _auth_message(connection, token: str) -> bool:
    try:
        raw = await asyncio.wait_for(connection.recv(), _AUTH_TIMEOUT_S)
    except Exception:
        await connection.close(code=1008)
        return False
    if not isinstance(raw, str):
        await connection.close(code=1008)
        return False
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await connection.close(code=1008)
        return False
    presented = str(msg.get("token") or "") if msg.get("type") == "auth" else ""
    if not presented or not bearer_matches(presented, token):
        LOG.info("monitor auth failed")
        await connection.close(code=1008)
        return False
    return True


def _apply_listen(raw: object, monitor: CallMonitor, sub: Subscription) -> None:
    if not isinstance(raw, str) or len(raw) > 4096:
        return
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if msg.get("type") != "listen":
        return
    enabled = bool(msg.get("enabled"))
    monitor.set_listen(sub, enabled)
    sub.events.put_nowait({"v": 1, "type": "listen", "enabled": enabled})


async def _session(connection, monitor: CallMonitor, sub: Subscription) -> None:
    """One writer. A blocked send fills the audio queue and the call drops frames."""
    recv_task: asyncio.Task = asyncio.create_task(connection.recv())
    event_task: asyncio.Task = asyncio.create_task(sub.events.get())
    audio_task: asyncio.Task = asyncio.create_task(sub.audio.get())
    tasks = {recv_task, event_task, audio_task}
    try:
        while True:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if recv_task in done:
                tasks.remove(recv_task)
                try:
                    raw = recv_task.result()
                except Exception:
                    return
                _apply_listen(raw, monitor, sub)
                recv_task = asyncio.create_task(connection.recv())
                tasks.add(recv_task)
            if event_task in done:
                tasks.remove(event_task)
                await connection.send(json.dumps(event_task.result()))
                event_task = asyncio.create_task(sub.events.get())
                tasks.add(event_task)
            if audio_task in done:
                tasks.remove(audio_task)
                frame = audio_task.result()
                if sub.listen:
                    await connection.send(frame)
                audio_task = asyncio.create_task(sub.audio.get())
                tasks.add(audio_task)
    finally:
        for task in tasks:
            task.cancel()
