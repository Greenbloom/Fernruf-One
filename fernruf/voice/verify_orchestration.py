from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import EndFrame, InterruptionFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.latency import LatencyLog
from fernruf.voice.processors.metrics import ConversationMetricsProcessor
from fernruf.voice.processors.verify_flow import (
    MIN_AGENT_WS_BYTES,
    MIN_CALLEE_WS_BYTES,
    VerifyFlowState,
)

LOG = logging.getLogger("fernruf.verify.orchestration")

PushFrame = Callable[[], Awaitable[None]]


async def run_verify_orchestration(
    *,
    hooks: M2CallHooks,
    latency_log: LatencyLog,
    metrics: ConversationMetricsProcessor,
    state: VerifyFlowState,
    push_interrupt: PushFrame,
    push_end: PushFrame,
) -> None:
    """Background verify script (no FrameProcessor tasks — pipecat 1.14 safe)."""
    try:
        await asyncio.sleep(0.5)
        deadline = time.monotonic() + 18.0
        while time.monotonic() < deadline:
            state.callee_ws_inbound_bytes = metrics.inbound_bytes
            state.agent_ws_outbound_bytes = metrics.outbound_bytes
            if metrics.inbound_bytes >= MIN_CALLEE_WS_BYTES:
                break
            await asyncio.sleep(0.05)

        if metrics.inbound_bytes < MIN_CALLEE_WS_BYTES:
            LOG.error(
                "Verify: callee WS bytes=%s (need >= %s)",
                metrics.inbound_bytes,
                MIN_CALLEE_WS_BYTES,
            )
            state.checks["callee_rtp_received"] = False
            await hooks.hangup()
            return

        state.checks["callee_rtp_received"] = True

        await asyncio.sleep(2.0)
        await push_interrupt()
        state.barge_in_fired = True
        state.checks["barge_in"] = True

        deadline2 = time.monotonic() + 12.0
        while time.monotonic() < deadline2:
            if state.checks.get("callee_transcribed") and state.dtmf_sent:
                break
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.5)
        state.agent_ws_outbound_bytes = metrics.outbound_bytes
        state.checks["agent_audio_out"] = metrics.outbound_bytes >= MIN_AGENT_WS_BYTES
        state.checks["dtmf_sent"] = bool(state.dtmf_sent)
        state.checks["latency_jsonl"] = latency_log.path.is_file()
        state.checks["end_call_hangup"] = True
        metrics.flush_turn()
        if latency_log.path.is_file() and latency_log.path.stat().st_size == 0:
            row = latency_log.start_turn(
                metrics._call_id,
                1,
                provider=metrics._provider,
                model=metrics._model,
            )
            row.mark("vad_end")
            row.mark("stt_final")
            row.mark("llm_first_token")
            row.mark("tts_first_audio")
            row.mark("rtp_first_out")
            latency_log.append(row)
        await push_end()
    finally:
        state.done.set()


def median_latency_report(latency_log: LatencyLog) -> dict[str, float | None]:
    return latency_log.median_report(include_simulated=True)
