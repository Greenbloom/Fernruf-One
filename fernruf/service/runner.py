"""Run one accepted call on the existing M2 path. Tests pass a substitute."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.runner")


@dataclass
class CallJob:
    call_id: str
    to_e164: str
    task_text: str
    max_minutes: int
    model: str | None
    settings: Settings
    shutdown: asyncio.Event
    record: bool = False
    dtmf_prefix: str | None = None
    language: str | None = None
    detection_text: str = ""
    goal: str = ""
    must_say: tuple[str, ...] = ()


@dataclass
class CallRunResult:
    outcome: str
    answered: bool
    transcript: list[dict] = field(default_factory=list)
    call_language: str = "de"
    llm_model: str = ""
    llm_model_source: str = ""
    duration_s: float | None = None
    answered_at: str | None = None


async def run_m2_or_simulate(job: CallJob) -> CallRunResult:
    """Simulated path when asked, otherwise the live M2 ARI pipeline in-process."""
    if job.shutdown.is_set():
        return CallRunResult(outcome="canceled", answered=False)
    if job.settings.m3_simulate or job.settings.voice_mock:
        return await _run_simulate(job)
    return await _run_live(job)


async def _run_simulate(job: CallJob) -> CallRunResult:
    from fernruf.voice.simulate import SimulateConversation

    sim = SimulateConversation(
        job.settings,
        job.task_text,
        llm_model_override=job.model,
        language=job.language,
        detection_text=job.detection_text or None,
    )
    sim.call_id = job.call_id
    task = asyncio.create_task(sim.run())
    stop = asyncio.create_task(job.shutdown.wait())
    try:
        done, _pending = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not task.done():
            task.cancel()
        stop.cancel()
        await asyncio.gather(task, stop, return_exceptions=True)
    if job.shutdown.is_set() and task.cancelled():
        return CallRunResult(outcome="canceled", answered=False, call_language=job.settings.call_language)
    if task.cancelled():
        return CallRunResult(outcome="canceled", answered=False)
    exc = task.exception()
    if exc is not None:
        raise exc
    result = task.result()
    transcript = [
        {"t_ms": line.t_ms, "speaker": line.speaker, "text": line.text} for line in result.transcript
    ]
    outcome = "hung_up_by_agent" if job.shutdown.is_set() else "completed"
    return CallRunResult(
        outcome=outcome,
        answered=True,
        transcript=transcript,
        call_language=result.call_language,
        llm_model=result.llm_model,
        llm_model_source=result.llm_model_source,
    )


async def _run_live(job: CallJob) -> CallRunResult:
    from fernruf.ari.m2 import M2AriSession
    from fernruf.dial import e164_to_sip_dial

    endpoint = f"PJSIP/{e164_to_sip_dial(job.to_e164)}@sip-trunk"
    ari = M2AriSession(
        base_url=job.settings.asterisk_ari_url,
        user=job.settings.asterisk_ari_user,
        password=job.settings.asterisk_ari_password,
    )
    try:
        out = await ari.run_m2_call(
            endpoint,
            caller_id=job.settings.ari_caller_id,
            settings=job.settings,
            task=job.task_text,
            max_minutes=job.max_minutes,
            llm_model_override=job.model,
            shutdown=job.shutdown,
            call_id=job.call_id,
            language=job.language,
            detection_text=job.detection_text or None,
            goal=job.goal,
            must_say=job.must_say,
        )
    finally:
        ari.close()
    answered = bool(out.get("answered"))
    duration = float(out.get("duration_s") or 0)
    if job.shutdown.is_set():
        outcome = "hung_up_by_agent" if answered else "canceled"
    elif not answered:
        outcome = "no_answer"
    elif duration >= job.max_minutes * 60 - 1:
        outcome = "max_duration"
    else:
        outcome = "completed"
    return CallRunResult(
        outcome=outcome,
        answered=answered,
        transcript=list(out.get("transcript") or []),
        call_language=str(out.get("call_language") or "de"),
        llm_model=str(out.get("llm_model") or ""),
        llm_model_source=str(out.get("llm_model_source") or ""),
        duration_s=duration,
        answered_at=str(out["answered_at"]) if answered and out.get("answered_at") else None,
    )
