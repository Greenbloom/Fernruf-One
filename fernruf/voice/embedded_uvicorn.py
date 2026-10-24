from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI

LOG = logging.getLogger(__name__)


def embedded_uvicorn_config(
    app: FastAPI,
    *,
    host: str,
    port: int,
    log_level: str = "warning",
) -> uvicorn.Config:
    """Minimal uvicorn config for a short-lived WS sidecar (no ASGI lifespan noise)."""
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        lifespan="off",
    )


def start_embedded_uvicorn(config: uvicorn.Config) -> tuple[uvicorn.Server, asyncio.Task[Any]]:
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    return server, task


async def stop_embedded_uvicorn(
    server: uvicorn.Server,
    task: asyncio.Task[Any],
    *,
    timeout: float = 15.0,
) -> None:
    """Signal uvicorn to exit and await serve(); avoid cancelling the task on success."""
    if task.done():
        with contextlib.suppress(Exception):
            task.result()
        return
    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        LOG.warning("embedded uvicorn did not stop within %.0fs", timeout)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    except asyncio.CancelledError:
        pass
    if not task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
