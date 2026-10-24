from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pipecat.frames.frames import ErrorFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.processors.llm_failure import LlmFailureProcessor


def test_llm_failure_speaks_on_non_fatal_upstream_error() -> None:
    hooks = MagicMock()
    hooks.hangup = AsyncMock()
    proc = LlmFailureProcessor(hooks=hooks)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]

    llm_proc = MagicMock()
    llm_proc.__class__.__name__ = "FernrufOpenAILLMService"

    async def run() -> None:
        await proc.process_frame(
            ErrorFrame(
                error="unexpected keyword argument 'reasoning'",
                fatal=False,
                processor=llm_proc,
            ),
            FrameDirection.UPSTREAM,
        )

    asyncio.run(run())
    texts = [getattr(f, "text", "") for f in pushed]
    assert any("technisches Problem" in t for t in texts)
    hooks.hangup.assert_not_called()


def test_llm_failure_hangup_after_two_errors() -> None:
    hooks = MagicMock()
    hooks.hangup = AsyncMock()
    proc = LlmFailureProcessor(hooks=hooks)
    pushed: list = []

    async def capture(frame, direction) -> None:  # noqa: ANN001
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]
    llm_proc = MagicMock()
    llm_proc.__class__.__name__ = "OpenAILLMService"
    err = ErrorFrame(error="boom", fatal=False, processor=llm_proc)

    async def run() -> None:
        await proc.process_frame(err, FrameDirection.UPSTREAM)
        await proc.process_frame(err, FrameDirection.UPSTREAM)

    asyncio.run(run())
    assert hooks.hangup.await_count == 1
    assert any("Wiederhören" in getattr(f, "text", "") for f in pushed)


def test_llm_failure_resets_after_text() -> None:
    hooks = MagicMock()
    hooks.hangup = AsyncMock()
    proc = LlmFailureProcessor(hooks=hooks)
    llm_proc = MagicMock()
    llm_proc.__class__.__name__ = "FernrufOpenAILLMService"

    async def run() -> None:
        await proc.process_frame(
            ErrorFrame(error="e1", fatal=False, processor=llm_proc),
            FrameDirection.UPSTREAM,
        )
        await proc.process_frame(
            LLMTextFrame(text="Hallo."),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            ErrorFrame(error="e2", fatal=False, processor=llm_proc),
            FrameDirection.UPSTREAM,
        )

    asyncio.run(run())
    hooks.hangup.assert_not_called()
