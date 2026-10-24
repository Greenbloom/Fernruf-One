"""Shared helpers to drive ``build_m2_pipeline`` in tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

from pipecat.frames.frames import BotStoppedSpeakingFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat_asterisk import AsteriskWebsocketTransport

from fernruf.voice.transport_teardown import cancel_asterisk_output_tasks


def unblock_asterisk_output(
    transport: AsteriskWebsocketTransport,
    *,
    end_call_guard: FrameProcessor | None = None,
) -> None:
    out = transport.output()
    if getattr(out, "_client", None) is not None:
        out._client.send = AsyncMock()
    real_push = out.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        if isinstance(frame, TTSStoppedFrame) and getattr(out, "_flow_controller", None):
            out._flow_controller.bot_stopped_speaking_fence.set()

            async def emit_bot_stopped() -> None:
                await asyncio.sleep(0.25)
                bot_stopped = BotStoppedSpeakingFrame()
                if end_call_guard is not None:
                    await end_call_guard.push_frame(bot_stopped, FrameDirection.DOWNSTREAM)
                else:
                    await real_push(bot_stopped, FrameDirection.UPSTREAM)

            asyncio.create_task(emit_bot_stopped())
        await real_push(frame, direction)

    out.push_frame = push  # type: ignore[method-assign]


def wire_transport_bot_stopped_to_guard(
    transport: AsteriskWebsocketTransport,
    end_call_guard: FrameProcessor,
    *,
    delay_s: float = 0.45,
) -> None:
    """After each TTS stop, emit BotStoppedSpeaking into the end-call guard."""
    out = transport.output()
    real_push = out.push_frame

    async def delayed_bot_stopped(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        await real_push(frame, direction)
        if isinstance(frame, TTSStoppedFrame) and getattr(out, "_flow_controller", None):

            async def emit_bot_stopped() -> None:
                await asyncio.sleep(delay_s)
                out._flow_controller.bot_stopped_speaking_fence.set()
                await end_call_guard.process_frame(
                    BotStoppedSpeakingFrame(),
                    FrameDirection.DOWNSTREAM,
                )

            asyncio.create_task(emit_bot_stopped())

    out.push_frame = delayed_bot_stopped  # type: ignore[method-assign]


async def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float = 8.0,
    interval_s: float = 0.05,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval_s)
    raise TimeoutError("condition not met within timeout")


async def prime_transport_flow(transport: AsteriskWebsocketTransport) -> None:
    await asyncio.sleep(0.35)
    out = transport.output()
    fc = getattr(out, "_flow_controller", None)
    if fc is not None:
        fc.bot_stopped_speaking_fence.set()
        task = getattr(fc, "_flow_control", None)
        if task is not None and not task.done():
            task.cancel()
