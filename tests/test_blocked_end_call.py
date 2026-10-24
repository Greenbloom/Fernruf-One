from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fernruf.settings import Settings
from pipecat.frames.frames import FunctionCallResultProperties

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools


def test_blocked_end_call_tool_result_tells_model_call_continues() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [{"role": "assistant", "content": "Können Sie das kurz bestätigen?"}]
    )
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=10,
        min_call_duration_before_hangup_s=999.0,
    )
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=AsyncMock(), session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    seen: list = []

    async def result_callback(result, *, properties=None):  # noqa: ANN001
        seen.append((result, properties))

    params = MagicMock()
    params.context = None
    params.tool_call_id = "blocked-1"
    params.result_callback = result_callback

    asyncio.run(end_call(params, reason="done", goodbye="Tschüss!"))
    result, props = seen[0]
    assert result["ok"] is False
    assert isinstance(props, FunctionCallResultProperties)
    assert props.run_llm is False
    assert session.pending_tool_tts == []
    assert session.phase.is_ending is False
