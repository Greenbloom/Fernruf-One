from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import WebSocket
from pipecat.frames.frames import EndFrame, InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner

from fernruf.llm.extra import LlmReasoningMode
from fernruf.settings import Settings
from fernruf.voice.asterisk_transport import FernrufAsteriskTransport
from fernruf.voice.call_log import attach_call_log_handler, detach_call_log_handler
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.latency import LatencyLog
from fernruf.voice.m2_pipeline import build_m2_pipeline
from fernruf.voice.pipecat_log_guard import PipecatLogGuard
from fernruf.voice.transport_teardown import cancel_asterisk_output_tasks_async
from fernruf.voice.processors.verify_flow import VerifyFlowState
from fernruf.voice.verify_orchestration import run_verify_orchestration

LOG = logging.getLogger("fernruf.voice.pipecat")


async def run_pipecat_on_websocket(
    websocket: WebSocket,
    *,
    settings: Settings,
    task: str,
    call_id: str,
    hooks: M2CallHooks,
    on_hangup: Callable[[], None] | None = None,
    llm_model_override: str | None = None,
    llm_reasoning_override: LlmReasoningMode | None = None,
    verify: bool = False,
    verify_state: VerifyFlowState | None = None,
    external_shutdown: asyncio.Event | None = None,
    goal: str = "",
    must_say: tuple[str, ...] | None = None,
    opening_request: str = "",
) -> VerifyFlowState | None:
    transport = FernrufAsteriskTransport(websocket=websocket)
    latency_log = LatencyLog(Path(settings.latency_log_path))
    session = hooks.session if hooks.session is not None else M2CallSession()
    built = build_m2_pipeline(
        transport,
        settings=settings,
        task=task,
        call_id=call_id,
        hooks=hooks,
        session=session,
        llm_model_override=llm_model_override,
        llm_reasoning_override=llm_reasoning_override,
        latency_log=latency_log,
        verify=verify,
        verify_state=verify_state,
        goal=goal,
        must_say=must_say,
        opening_request=opening_request,
    )
    await built.stt.preload()

    call_log_path = attach_call_log_handler(call_id)
    LOG.info("Call log file: %s", call_log_path)

    log_guard = PipecatLogGuard()
    log_guard.install()

    verify_task: asyncio.Task[None] | None = None
    if verify and built.verify_state and built.verify_processor:

        async def push_interrupt() -> None:
            await built.verify_processor.push_frame(
                InterruptionFrame(), FrameDirection.DOWNSTREAM
            )

        async def push_end() -> None:
            await built.verify_processor.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)

        verify_task = asyncio.create_task(
            run_verify_orchestration(
                hooks=hooks,
                latency_log=latency_log,
                metrics=built.metrics,
                state=built.verify_state,
                push_interrupt=push_interrupt,
                push_end=push_end,
            )
        )

    params = PipelineParams(
        audio_in_sample_rate=16000,
        audio_out_sample_rate=16000,
        allow_interruptions=True,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(built.pipeline, params=params)
    shutdown_lock = asyncio.Lock()
    shutdown_done = False

    async def request_pipeline_shutdown() -> None:
        nonlocal shutdown_done
        async with shutdown_lock:
            if shutdown_done:
                return
            shutdown_done = True
        LOG.info("pipeline shutdown requested call=%s", call_id)
        with contextlib.suppress(Exception):
            await worker.stop_when_done()
        with contextlib.suppress(Exception):
            await websocket.close()

    hooks.request_pipeline_shutdown = request_pipeline_shutdown

    async def _watch_external_shutdown() -> None:
        if external_shutdown is None:
            return
        await external_shutdown.wait()
        await request_pipeline_shutdown()

    shutdown_watch_task = asyncio.create_task(_watch_external_shutdown())

    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        built.metrics.flush_turn()
        summary = latency_log.median_report(include_simulated=False)
        LOG.info("call latency summary: %s", json.dumps(summary, ensure_ascii=False))
        detach_call_log_handler(call_id)
        await cancel_asterisk_output_tasks_async(transport)
        shutdown_watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_watch_task
        if not shutdown_done and not worker.has_finished():
            with contextlib.suppress(RuntimeError, Exception):
                await worker.stop_when_done()
        if not worker.has_finished():
            with contextlib.suppress(RuntimeError, Exception):
                await worker.cancel()
        log_guard.remove()
        if verify_task:
            with asyncio.timeout(25.0):
                await verify_task
        if log_guard.count:
            LOG.error("pipecat logged %s ERROR line(s)", log_guard.count)
            for line in log_guard.error_lines[:20]:
                LOG.error("pipecat: %s", line)
            if verify and built.verify_state:
                built.verify_state.checks["pipecat_errors"] = False
            if verify:
                raise RuntimeError(
                    f"pipecat logged {log_guard.count} ERROR line(s); verify cannot pass"
                )
    if verify and built.verify_state:
        await built.verify_state.done.wait()
    if on_hangup:
        on_hangup()
    return built.verify_state
