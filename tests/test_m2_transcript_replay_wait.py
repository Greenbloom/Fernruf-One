"""Replay of the 2026-11-12 call where a wait request ended the call."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from pipecat.frames.frames import InputTransportMessageFrame, LLMMessagesAppendFrame, StartFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.workers.runner import WorkerRunner
from tests.test_call_phase_flows import (
    _last_user,
    _pipeline,
    _ScriptLLM,
    _start_script,
    _stop_pipeline,
    _tool_end_call,
)

WAIT = "Warte, ich teste eben die Knöpfe oben rechts."
OBJECTION = "Na warte, du kannst das noch nicht auflegen."
FOLLOW_UP = "Ich habe noch eine Frage an dich."
ASKED = "Hat sich das Fenster von selbst geöffnet, und funktionieren die Knöpfe oben rechts?"
TASK = (
    "Frage, ob sich das Fenster von selbst geöffnet hat und ob die Knöpfe "
    "oben rechts funktionieren, dann verabschiede dich."
)


def _reply(body: dict) -> list[dict]:
    user = _last_user(body)
    if user == WAIT:
        chunks = [{"delta": {"content": "Verstehe, ich warte kurz."}}]
        chunks.extend(_tool_end_call("Testanruf abgeschlossen", "Auf Wiederhören."))
        return chunks
    if "Frage" in user:
        return [
            {
                "delta": {"content": "Ja, gerne. Was möchten Sie wissen?"},
                "finish_reason": "stop",
            }
        ]
    if user.startswith("Ja, das Fenster"):
        return _tool_end_call("Fenster und Knöpfe bestätigt", "Auf Wiederhören.")
    return [{"delta": {"content": ASKED}, "finish_reason": "stop"}]


async def _boot(built, transport):
    params = PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    worker = PipelineWorker(built.pipeline, params=params, cancel_timeout_secs=1.0)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await worker.queue_frame(StartFrame())
    await worker.queue_frame(
        InputTransportMessageFrame(
            message={"event": "MEDIA_START", "ptime": 20, "optimal_frame_size": 640}
        )
    )
    from tests.m2_pipeline_harness import prime_transport_flow

    await prime_transport_flow(transport)
    return worker, run_task, built.context_pair.user()


async def _push_user(user_agg, line: str) -> None:
    await user_agg.push_frame(
        LLMMessagesAppendFrame(
            messages=[{"role": "user", "content": line}],
            run_llm=True,
        ),
        FrameDirection.DOWNSTREAM,
    )


def test_replay_wait_does_not_hang_up_and_asks_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="fernruf")
    server, base = _start_script(_reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task=TASK,
        hold_max_s=0.2,
        fish_audio_model="drama-3",
    )

    async def run() -> None:
        worker, run_task, user_agg = await _boot(built, transport)
        try:
            before = len(_ScriptLLM.requests)
            await _push_user(user_agg, "Hallo.")
            from tests.m2_pipeline_harness import wait_until

            await wait_until(lambda: len(_ScriptLLM.requests) > before, timeout_s=8.0)
            await _push_user(user_agg, WAIT)
            await asyncio.sleep(1.2)
            assert hangup.await_count == 0
            assert not session.phase.is_ending
            assert not session.phase.is_hung_up
            spoken = [r.message for r in caplog.records if r.message.startswith("TTS start:")]
            assert not any("Verstehe, ich warte" in line for line in spoken)
            assert not any("Auf Wiederhören" in line for line in spoken)
            assert any("Frage noch einmal" in line or "ask again" in line for line in spoken)
            before_follow = len(_ScriptLLM.requests)
            await _push_user(user_agg, FOLLOW_UP)
            await wait_until(lambda: len(_ScriptLLM.requests) > before_follow, timeout_s=8.0)
            await asyncio.sleep(0.4)
            assert hangup.await_count == 0
            assert not session.phase.is_hung_up
        finally:
            await _stop_pipeline(transport, worker, run_task)

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
    assert any("fish_model=drama-3" in r.message for r in caplog.records)


def test_replay_speech_during_playout_continues_the_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server, base = _start_script(_reply)
    built, transport, session, hangup = _pipeline(
        tmp_path,
        base,
        timeout_s=5.0,
        fallback="",
        task=TASK,
        hangup_margin_s=2.0,
    )
    caplog.set_level(logging.INFO)

    async def run() -> None:
        from tests.m2_pipeline_harness import wait_until

        worker, run_task, user_agg = await _boot(built, transport)
        try:
            before = len(_ScriptLLM.requests)
            await _push_user(user_agg, "Hallo.")
            await wait_until(lambda: len(_ScriptLLM.requests) > before, timeout_s=8.0)
            await _push_user(
                user_agg,
                "Ja, das Fenster hat sich von selbst geöffnet und die Knöpfe funktionieren.",
            )
            await wait_until(
                lambda: session.phase.is_ending or session.phase.is_hung_up,
                timeout_s=8.0,
            )
            requests_at_end = len(_ScriptLLM.requests)
            await _push_user(user_agg, OBJECTION)
            await _push_user(user_agg, FOLLOW_UP)
            await wait_until(lambda: len(_ScriptLLM.requests) > requests_at_end, timeout_s=8.0)
            await asyncio.sleep(0.5)
            assert hangup.await_count == 0
            assert not session.phase.is_hung_up
            assert not any(
                "No assistant turn after end_call" in r.message and FOLLOW_UP in r.message
                for r in caplog.records
            )
        finally:
            await _stop_pipeline(transport, worker, run_task)

    try:
        asyncio.run(run())
    finally:
        server.shutdown()
