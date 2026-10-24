from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fernruf.settings import Settings
from fernruf.tts import fish_ws_session as mod
from fernruf.tts.fish_ws_session import FishLiveWsSession


def test_ws_connect_failure_enables_rest_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, fish_transport="websocket")

    async def _fail(*_a: Any, **_k: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(mod.websockets, "connect", _fail)
    session = FishLiveWsSession(
        settings=settings,
        api_key="test-key",
        model="s2.1-pro-free",
        voice_id=None,
        ws_url="ws://127.0.0.1:9/invalid",
    )
    asyncio.run(session.connect())
    assert session.fallback_rest is True
