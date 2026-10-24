from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pipecat.processors.aggregators.llm_context import LLMContext

from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools


def test_end_call_sees_user_turns_from_tool_context() -> None:
    session = M2CallSession()
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=2,
        min_call_duration_before_hangup_s=0.0,
    )
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    ctx = LLMContext(
        messages=[
            {"role": "user", "content": "Na, wie geht's dir?"},
            {"role": "assistant", "content": "Gut."},
            {"role": "user", "content": "Das klappt."},
        ]
    )
    seen: list = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        seen.append(result)

    params = MagicMock()
    params.context = ctx
    params.result_callback = result_callback

    asyncio.run(
        end_call(
            params,
            reason="Test ok",
            goodbye="Vielen Dank für den Testanruf. Auf Wiederhören!",
        )
    )
    assert seen[0]["ok"] is True
    assert session.user_turn_count >= 2
