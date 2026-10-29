"""Finish demo-call without waiting for speech-runtime threads.

faster-whisper and the VAD leave non-daemon threads behind. ``asyncio.run``
joins them on the way out, so the CLI sat there until it was killed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Coroutine
from typing import Any


def run_until_done(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run ``coro`` and return. Do not join leftover executor threads."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        _drop_loop(loop)


def _drop_loop(loop: asyncio.AbstractEventLoop) -> None:
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            # A task that ignores cancellation must not keep the CLI alive.
            with contextlib.suppress(Exception):
                loop.run_until_complete(
                    asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=2.0,
                    )
                )
        with contextlib.suppress(Exception):
            loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), timeout=2.0))
        executor = getattr(loop, "_default_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
            loop._default_executor = None  # type: ignore[attr-defined]
    finally:
        with contextlib.suppress(Exception):
            loop.close()
        with contextlib.suppress(Exception):
            asyncio.set_event_loop(None)


def exit_demo_process(code: int) -> None:
    """Exit now. A normal return still waits on those runtime threads."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.environ.get("FERNRUF_DEMO_KEEP_PROCESS") == "1":
        raise SystemExit(code)
    os._exit(code)
