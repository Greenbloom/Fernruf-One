from __future__ import annotations

import asyncio

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.processors.context_session_sync import ContextSessionSyncProcessor


def test_sync_from_llm_context_counts_user_turns_and_ciao() -> None:
    session = M2CallSession()
    sync = ContextSessionSyncProcessor(session=session)
    ctx = LLMContext(
        messages=[
            {"role": "user", "content": "Ja, ich bin hier."},
            {"role": "assistant", "content": "Gut."},
            {"role": "user", "content": "Ciao."},
        ]
    )

    async def run() -> None:
        await sync.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    assert session.user_turn_count == 2
    assert session.callee_goodbye_seen
    assert session.last_user_text == "Ciao."


def test_du_kannst_auflegen_sets_callee_requested_end() -> None:
    session = M2CallSession()
    session.sync_from_llm_messages(
        [{"role": "user", "content": "Kein Problem, du kannst es auflegen."}]
    )
    assert session.callee_requested_end
    assert session.user_turn_count == 1
