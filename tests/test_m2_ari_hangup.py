from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fernruf.ari.client import AriSession
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.processors.call_hangup import CallHangup


def test_ari_delete_channel_logs_status() -> None:
    response = MagicMock(status_code=204)
    client = MagicMock()
    client.delete.return_value = response
    ari = AriSession(
        base_url="http://127.0.0.1:8088",
        user="u",
        password="p",
    )
    ari._client = client
    ari.hangup("pstn-chan-1")
    client.delete.assert_called_once_with("/channels/pstn-chan-1")


def test_end_call_execute_hangup_before_pipeline_shutdown() -> None:
    session = M2CallSession()
    session.phase.begin_ending(reason="test", await_playback=False)
    hangup = AsyncMock()
    shutdown = AsyncMock()
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    guard = CallHangup(
        hooks=M2CallHooks(
            send_dtmf=AsyncMock(),
            hangup=hangup,
            session=session,
            request_pipeline_shutdown=shutdown,
        ),
        session=session,
        settings=settings,
    )
    guard.push_frame = AsyncMock()  # type: ignore[method-assign]
    order: list[str] = []

    async def tracked_hangup() -> None:
        order.append("hangup")
        await hangup()

    async def tracked_shutdown() -> None:
        order.append("shutdown")
        await shutdown()

    guard._hooks.hangup = tracked_hangup
    guard._hooks.request_pipeline_shutdown = tracked_shutdown
    asyncio.run(guard._execute_hangup())
    assert order == ["hangup", "shutdown"]


