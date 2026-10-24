"""Subprocess entry: run mock pipecat until SIGINT, then exit 0 if shutdown is clean."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from unittest.mock import AsyncMock, MagicMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.pipecat_runner import run_pipecat_on_websocket


async def _main() -> int:
    settings = Settings(
        _env_file=None,
        llm_mock=True,
        voice_mock=True,
        latency_log_path="/tmp/fernruf-sigint-test.jsonl",
    )
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        if not shutdown.is_set():
            shutdown.set()
        else:
            raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

    print("READY", flush=True)

    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()

    await run_pipecat_on_websocket(
        ws,
        settings=settings,
        task="sigint test",
        call_id="sigint1",
        hooks=hooks,
        external_shutdown=shutdown,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        raise SystemExit(1) from None
