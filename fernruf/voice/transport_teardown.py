from __future__ import annotations

import asyncio
import contextlib
import logging

from pipecat.frames.frames import CancelFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat_asterisk import AsteriskWebsocketTransport

LOG = logging.getLogger("fernruf.voice.transport_teardown")

_ASTERISK_OUTPUT_TASK_ATTRS = (
    "_clock_task_handler",
    "_audio_task_handler",
    "_clock_task",
    "_audio_task",
)


def _cancel_tasks_on(obj: object) -> None:
    for name in dir(obj):
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if isinstance(val, asyncio.Task) and not val.done():
            val.cancel()
            LOG.debug("Cancelled task %s on %s", name, type(obj).__name__)
    for attr in _ASTERISK_OUTPUT_TASK_ATTRS:
        try:
            val = getattr(obj, attr, None)
        except Exception:
            continue
        if isinstance(val, asyncio.Task) and not val.done():
            val.cancel()
            LOG.debug("Cancelled asterisk task %s on %s", attr, type(obj).__name__)
    tm = getattr(obj, "_task_manager", None) or getattr(obj, "task_manager", None)
    if tm is not None:
        for name in dir(tm):
            try:
                val = getattr(tm, name)
            except Exception:
                continue
            if isinstance(val, asyncio.Task) and not val.done():
                val.cancel()


async def _await_task_cancellations(obj: object) -> None:
    pending: list[asyncio.Task] = []
    for name in dir(obj):
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if isinstance(val, asyncio.Task) and not val.done():
            val.cancel()
            pending.append(val)
    for attr in _ASTERISK_OUTPUT_TASK_ATTRS:
        try:
            val = getattr(obj, attr, None)
        except Exception:
            continue
        if isinstance(val, asyncio.Task) and not val.done():
            val.cancel()
            pending.append(val)
    if pending:
        with contextlib.suppress(Exception):
            await asyncio.gather(*pending, return_exceptions=True)


async def cancel_asterisk_output_tasks_async(transport: AsteriskWebsocketTransport) -> None:
    """Cancel background clock/audio tasks on the Asterisk output transport."""
    out = transport.output()
    if isinstance(out, FrameProcessor):
        cancel = getattr(out, "cancel", None)
        if callable(cancel):
            with contextlib.suppress(Exception):
                await cancel(CancelFrame())
        cancel_task = getattr(out, "cancel_task", None)
        if callable(cancel_task):
            with contextlib.suppress(Exception):
                cancel_task()
    targets: list[object] = [
        out,
        getattr(out, "_flow_controller", None),
        getattr(out, "_client", None),
        getattr(out, "_media_sender", None),
    ]
    senders = getattr(out, "_media_senders", None)
    if isinstance(senders, dict):
        targets.extend(senders.values())
    for target in targets:
        if target is not None:
            _cancel_tasks_on(target)
            await _await_task_cancellations(target)


def cancel_asterisk_output_tasks(transport: AsteriskWebsocketTransport) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(cancel_asterisk_output_tasks_async(transport))
        return
    loop.create_task(cancel_asterisk_output_tasks_async(transport))
