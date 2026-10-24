from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.pipecat_runner import run_pipecat_on_websocket


def test_fake_hangup_ends_pipecat_run_within_2s() -> None:
    settings = Settings(
        _env_file=None,
        llm_mock=True,
        voice_mock=True,
        latency_log_path="/tmp/fernruf-test-latency.jsonl",
    )
    session = M2CallSession()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive = AsyncMock(side_effect=asyncio.CancelledError)
    ws.send = AsyncMock()

    async def run() -> None:
        task = asyncio.create_task(
            run_pipecat_on_websocket(
                ws,
                settings=settings,
                task="shutdown test",
                call_id="shut1",
                hooks=hooks,
            )
        )
        for _ in range(200):
            if hooks.request_pipeline_shutdown is not None:
                break
            await asyncio.sleep(0.05)
        assert hooks.request_pipeline_shutdown is not None
        t0 = time.monotonic()
        await hooks.request_pipeline_shutdown()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert time.monotonic() - t0 < 2.0
        ws.close.assert_awaited()

    asyncio.run(run())
