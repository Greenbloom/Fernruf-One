from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools
from fernruf.voice.processors.call_hangup import CallHangup


def test_replay_1344_no_false_question_block_after_answered_turn() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Ist die Verbindung gut verständlich?"},
            {"role": "user", "content": "Ja, alles super."},
            {
                "role": "assistant",
                "content": "Perfekt, dann ist die Verbindung in beide Richtungen sauber.",
            },
        ]
    )
    settings = Settings(_env_file=None, min_call_duration_before_hangup_s=0.0)
    assert (
        session.validate_end_call(
            "Test abgeschlossen",
            settings,
            goodbye="Vielen Dank für den Test und auf Wiederhören.",
        )
        is None
    )


def test_replay_1344_dict_goodbye_hangup_after_mutual_farewell() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [
            {"role": "assistant", "content": "Vielen Dank für den Test. Auf Wiederhören!"},
            {"role": "user", "content": "Ja, auf Wiederhören."},
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
    end_call = _llm_tools(hooks, session, settings)[1]
    params = AsyncMock()
    params.context = None
    params.tool_call_id = "call_1344"
    params.result_callback = AsyncMock()

    async def run() -> None:
        await end_call(params, reason="done", goodbye={})
        await asyncio.sleep(0.15)
        assert hangup.await_count == 1

    asyncio.run(run())
