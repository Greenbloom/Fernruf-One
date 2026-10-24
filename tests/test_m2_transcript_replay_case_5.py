from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools
from fernruf.voice.processors.call_hangup import CallHangup

GOODBYE_TURN = (
    "Perfekt, das freut mich. Dann ist die Verbindung gut. "
    "Ich melde mich mit einem kurzen Testanruf, um zu prüfen, ob alles funktioniert – "
    "und das hat geklappt. Vielen Dank für Ihre Zeit und auf Wiederhören!"
)


def test_replay_1141_context_goodbye_allows_empty_goodbye_end_call() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": GOODBYE_TURN},
            {"role": "user", "content": "Auf Wiederhören."},
        ]
    )
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert session.validate_end_call("Test ok", settings, goodbye="") is None


def test_replay_1141_empty_goodbye_hangup_via_schedule_hook() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": GOODBYE_TURN},
            {"role": "user", "content": "Auf Wiederhören."},
        ]
    )
    settings = Settings(
        _env_file=None,
        hangup_after_bot_stopped_margin_s=0.05,
        hangup_no_audio_fallback_s=0.2,
        min_call_duration_before_hangup_s=0.0,
    )
    hangup = AsyncMock()
    guard = CallHangup(
        hooks=M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session),
        session=session,
        settings=settings,
    )
    guard.push_frame = AsyncMock()  # type: ignore[method-assign]

    hooks = M2CallHooks(
        send_dtmf=AsyncMock(),
        hangup=hangup,
        session=session,
        schedule_hangup=guard.arm,
    )
    tools = _llm_tools(hooks, session, settings)
    end_call = tools[1]

    params = AsyncMock()
    params.context = None
    params.tool_call_id = "call_1141"
    params.result_callback = AsyncMock()

    async def run() -> None:
        await end_call(params, reason="Test ok", goodbye="")
        await asyncio.sleep(0.15)
        assert hangup.await_count == 1

    asyncio.run(run())
