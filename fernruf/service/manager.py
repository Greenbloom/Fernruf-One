"""One call at a time. Policy runs here, then the existing M2 pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fernruf.audit import append_audit_event
from fernruf.call_settings.model import validate_llm_model_id
from fernruf.call_settings.resolver import llm_model_missing_reason, resolve_call_llm_model
from fernruf.call_settings.store import get_call_settings
from fernruf.dial import normalize_e164
from fernruf.language import normalize_language_code, task_detection_text
from fernruf.quiet_hours import BERLIN, parse_quiet_hours
from fernruf.service.costs import estimate_cost
from fernruf.service.line_health import LineHealth
from fernruf.service.policy_gate import (
    PolicyLimits,
    ServiceDecision,
    SpendSnapshot,
    evaluate_service_policy,
)
from fernruf.service.records import get_stored_call, list_stored_calls, spend_snapshot, upsert_call
from fernruf.service.runner import CallJob, CallRunResult, run_m2_or_simulate
from fernruf.service.settings_update import monthly_ceiling, per_call_ceiling
from fernruf.principal import dial_blocked_placeholder_reason
from fernruf.settings import Settings, validate_operational_settings

LOG = logging.getLogger("fernruf.manager")

ProgressFn = Callable[[float, float, str], Awaitable[None]]
Runner = Callable[[CallJob], Awaitable[CallRunResult]]


class CallError(Exception):
    def __init__(self, code: str, message: str, *, extra: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


def format_task(task: dict, *, persona: str | None, language: str | None, dtmf_prefix: str | None) -> str:
    goal = str(task.get("goal") or "").strip()
    lines = [f"Ziel: {goal}"]
    if task.get("context"):
        lines.append(f"Kontext: {task['context']}")
    questions = task.get("questions") or []
    if questions:
        lines.append("Fragen: " + "; ".join(str(item) for item in questions))
    must_say = task.get("must_say") or []
    if must_say:
        lines.append("Muss sagen: " + "; ".join(str(item) for item in must_say))
    must_not = task.get("must_not") or []
    if must_not:
        lines.append("Darf nicht: " + "; ".join(str(item) for item in must_not))
    if task.get("decision_limits"):
        lines.append(f"Entscheidungsgrenzen: {task['decision_limits']}")
    if task.get("fallback"):
        lines.append(f"Fallback: {task['fallback']}")
    if persona:
        lines.append(f"Persona: {persona}")
    if language:
        lines.append(f"Call language: {language}. Speak only this language.")
    if dtmf_prefix:
        lines.append(f"DTMF prefix to send at the start: {dtmf_prefix}")
    lines.append("The fixed AI disclosure is spoken first and must not be changed.")
    return "\n".join(lines)


def limits_for(settings: Settings) -> PolicyLimits:
    record = get_call_settings(settings)
    ceiling = min(per_call_ceiling(settings), int(record.max_minutes))
    monthly = min(monthly_ceiling(settings), float(record.monthly_budget_eur))
    if record.mobile_flat_enabled:
        monthly = min(monthly, 10.0)
    allowlist = []
    for number in record.allowlist:
        try:
            allowlist.append(normalize_e164(number))
        except ValueError:
            continue
    return PolicyLimits(
        allowlist=frozenset(allowlist),
        international_enabled=bool(record.international_enabled),
        quiet_hours=parse_quiet_hours(
            record.quiet_hours,
            allow_off=settings.quiet_hours_allow_off,
        ),
        max_minutes_ceiling=max(1, ceiling),
        calls_per_day=int(settings.calls_per_day),
        minutes_per_day=float(settings.minutes_per_day),
        monthly_budget_eur=monthly,
        mobile_budget_eur=float(settings.mobile_budget_eur),
        mobile_flat_enabled=bool(record.mobile_flat_enabled),
        ai_eur_per_min=float(settings.ai_eur_per_min),
        mobile_eur_per_min=float(settings.mobile_eur_per_min),
    )


def public_call(row: dict[str, Any]) -> dict[str, Any]:
    lines = list(row.get("transcript") or [])
    status = row.get("status")
    return {
        "call_id": row.get("call_id"),
        "to": row.get("to"),
        "status": status,
        "outcome": row.get("outcome"),
        "started_at": row.get("started_at"),
        "answered_at": row.get("answered_at"),
        "ended_at": row.get("ended_at"),
        "duration_s": row.get("duration_s") or 0,
        "cost_estimate_eur": row.get("cost_estimate_eur") or 0,
        "call_language": row.get("call_language") or "de",
        "last_lines": lines[-10:],
        "transcript": {"lines": lines, "complete": status == "finalized"},
        "summary": row.get("summary"),
        "llm_model": row.get("llm_model") or "",
        "llm_model_source": row.get("llm_model_source") or "",
    }


class CallManager:
    def __init__(
        self,
        settings: Settings,
        *,
        line_health: LineHealth,
        runner: Runner | None = None,
        now: Callable[[], datetime] | None = None,
        keepalive_s: float = 5.0,
    ) -> None:
        self.settings = settings
        validate_operational_settings(settings)
        self.line_health = line_health
        self.runner = runner or run_m2_or_simulate
        self.now = now or (lambda: datetime.now(tz=BERLIN))
        self.keepalive_s = keepalive_s
        self._lock = asyncio.Lock()
        self._active: dict[str, Any] | None = None

    def _audit(self, event: dict) -> None:
        append_audit_event(Path(self.settings.audit_path), event)

    def _busy(self) -> bool:
        active = self._active
        if not active:
            return False
        task = active.get("task")
        return task is None or not task.done()

    def _snapshot(self) -> SpendSnapshot:
        raw = spend_snapshot(self.settings, now=self.now())
        return SpendSnapshot(
            calls_today=int(raw["calls_today"]),
            minutes_today=float(raw["minutes_today"]),
            spent_eur=float(raw["spent_eur"]),
            mobile_spent_eur=float(raw["mobile_spent_eur"]),
        )

    def balance(self) -> dict[str, Any]:
        limits = limits_for(self.settings)
        spent = self._snapshot()
        record = get_call_settings(self.settings)
        return {
            "monthly_budget_eur": limits.monthly_budget_eur,
            "spent_eur": spent.spent_eur,
            "remaining_eur": round(max(0.0, limits.monthly_budget_eur - spent.spent_eur), 4),
            "mobile_spent_eur": spent.mobile_spent_eur,
            "calls_today": spent.calls_today,
            "calls_today_max": limits.calls_per_day,
            "mobile_flat_enabled": record.mobile_flat_enabled,
        }

    def get_call(self, call_id: str) -> dict[str, Any]:
        row = get_stored_call(self.settings, call_id)
        if row is None:
            raise CallError("not_found", f"No call {call_id}.")
        return public_call(row)

    def list_calls(self, *, limit: int = 20, since: str | None = None) -> list[dict[str, Any]]:
        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=BERLIN)
        rows = []
        for row in list_stored_calls(self.settings):
            if since_dt is not None:
                raw = row.get("ended_at") or row.get("started_at")
                if not raw:
                    continue
                stamp = datetime.fromisoformat(raw)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=BERLIN)
                if stamp < since_dt:
                    continue
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            rows.append(
                {
                    "call_id": row.get("call_id"),
                    "to": row.get("to"),
                    "status": row.get("status"),
                    "outcome": row.get("outcome"),
                    "ended_at": row.get("ended_at"),
                    "duration_s": row.get("duration_s") or 0,
                    "summary": {
                        "one_line": summary.get("one_line"),
                        "task_status": summary.get("task_status"),
                    },
                }
            )
        rows.sort(key=lambda item: item.get("ended_at") or "", reverse=True)
        return rows[: max(1, min(limit, 100))]

    async def place(
        self,
        *,
        to: str,
        task: dict,
        persona: str | None = None,
        language: str | None = None,
        max_minutes: int | None = None,
        record: bool = False,
        dtmf_prefix: str | None = None,
        model: str | None = None,
        wait: bool = False,
        progress: ProgressFn | None = None,
        wait_seconds: float | None = None,
    ) -> dict[str, Any]:
        goal = str(task.get("goal") or "").strip()
        if not goal:
            raise CallError("invalid_task", "task.goal is required.")
        blocked = dial_blocked_placeholder_reason(self.settings)
        if blocked:
            raise CallError("principal_placeholder", blocked)
        missing_model = llm_model_missing_reason(self.settings)
        if missing_model:
            raise CallError("llm_model_missing", missing_model)
        if model is not None:
            try:
                model = validate_llm_model_id(model)
            except ValueError as exc:
                raise CallError("invalid_model", str(exc)) from exc
        limits = limits_for(self.settings)
        requested = limits.max_minutes_ceiling if max_minutes is None else int(max_minutes)
        if wait and wait_seconds is not None:
            if wait_seconds < 1:
                raise CallError("invalid_settings", "wait_seconds must be at least 1.")
            if wait_seconds > requested * 60:
                raise CallError(
                    "wait_too_long",
                    f"wait_seconds cannot exceed the call max of {requested * 60} seconds.",
                )
        async with self._lock:
            if self._busy():
                raise CallError(
                    "concurrent_call",
                    "Only one call can run at a time. Hang up or wait for it to finish.",
                )
            decision = evaluate_service_policy(
                to,
                task=task,
                requested_minutes=requested,
                limits=limits,
                spend=self._snapshot(),
                line_ready=self.line_health.ready(),
                now=self.now(),
            )
            self._audit(
                {
                    "event": "place_call",
                    "to": to,
                    "allowed": decision.allowed,
                    "error_code": decision.error_code,
                    "goal": goal[:200],
                }
            )
            if not decision.allowed:
                self._store_rejection(to, task, decision)
                raise CallError(decision.error_code or "rejected_by_policy", decision.message)
            try:
                e164 = normalize_e164(to)
            except ValueError as exc:
                raise CallError("invalid_number", "Number must be E.164.") from exc
            resolved = resolve_call_llm_model(self.settings, override=model)
            explicit_language = normalize_language_code(language or "") or None
            detection_text = task_detection_text(task)
            call_id = uuid.uuid4().hex[:12]
            started = self.now().isoformat()
            reserved = estimate_cost(
                e164,
                requested * 60,
                mobile_flat=limits.mobile_flat_enabled,
                ai_eur_per_min=limits.ai_eur_per_min,
                mobile_eur_per_min=limits.mobile_eur_per_min,
            )
            shutdown = asyncio.Event()
            done = asyncio.Event()
            upsert_call(
                self.settings,
                call_id,
                to=e164,
                status="queued",
                outcome=None,
                started_at=started,
                answered_at=None,
                ended_at=None,
                duration_s=0,
                cost_estimate_eur=reserved.total_eur,
                mobile_cost_eur=reserved.mobile_eur,
                ai_cost_eur=reserved.ai_eur,
                reserved_minutes=requested,
                counts_toward_caps=True,
                transcript=[],
                summary=None,
                task=format_task(task, persona=persona, language=language, dtmf_prefix=dtmf_prefix),
                record=bool(record),
                llm_model=resolved.model,
                llm_model_source=resolved.source,
                call_language=explicit_language or "de",
                policy_checks=decision.checks,
            )
            job = CallJob(
                call_id=call_id,
                to_e164=e164,
                task_text=format_task(task, persona=persona, language=language, dtmf_prefix=dtmf_prefix),
                max_minutes=requested,
                model=model,
                settings=self.settings,
                shutdown=shutdown,
                record=bool(record),
                dtmf_prefix=dtmf_prefix,
                language=explicit_language,
                detection_text=detection_text,
                goal=goal,
                must_say=tuple(
                    str(item).strip() for item in (task.get("must_say") or []) if str(item).strip()
                ),
            )
            active = {
                "call_id": call_id,
                "shutdown": shutdown,
                "done": done,
                "task": None,
                "max_minutes": requested,
            }
            self._active = active
            active["task"] = asyncio.create_task(self._execute(active, job, limits))
            payload = {
                "call_id": call_id,
                "status": "queued",
                "policy_checks": decision.checks,
            }
        if not wait:
            return payload
        return await self._wait(active, progress, wait_seconds, requested)

    def _store_rejection(self, to: str, task: dict, decision: ServiceDecision) -> None:
        outcome = "line_unavailable" if decision.error_code == "line_unavailable" else "rejected_by_policy"
        now = self.now().isoformat()
        upsert_call(
            self.settings,
            uuid.uuid4().hex[:12],
            to=to,
            status="finalized",
            outcome=outcome,
            error_code=decision.error_code,
            started_at=now,
            ended_at=now,
            duration_s=0,
            cost_estimate_eur=0,
            counts_toward_caps=False,
            transcript=[],
            summary=None,
            task=str(task.get("goal") or "")[:200],
        )

    async def _execute(self, active: dict[str, Any], job: CallJob, limits: PolicyLimits) -> None:
        call_id = job.call_id
        started = asyncio.get_running_loop().time()
        try:
            upsert_call(self.settings, call_id, status="dialing", dial_started=True)
            result = await self.runner(job)
            duration = result.duration_s
            if duration is None:
                duration = max(0.0, asyncio.get_running_loop().time() - started)
            actual = estimate_cost(
                job.to_e164,
                duration,
                mobile_flat=limits.mobile_flat_enabled,
                ai_eur_per_min=limits.ai_eur_per_min,
                mobile_eur_per_min=limits.mobile_eur_per_min,
            )
            ended_dt = self.now()
            ended = ended_dt.isoformat()
            row = get_stored_call(self.settings, call_id) or {}
            answered_at = None
            if result.answered:
                # The runner stamps the ARI answer. Simulate and stand-in
                # runners do not, so place the answer `duration` before hangup
                # on this clock. Never copy ended_at into answered_at.
                if result.answered_at:
                    answered_at = result.answered_at
                else:
                    span = duration if duration and duration > 0 else 0.001
                    answered_at = (ended_dt - timedelta(seconds=span)).isoformat()
            upsert_call(
                self.settings,
                call_id,
                status="finalized",
                outcome=result.outcome,
                answered_at=answered_at,
                ended_at=ended,
                duration_s=round(duration, 3),
                cost_estimate_eur=actual.total_eur,
                mobile_cost_eur=actual.mobile_eur,
                ai_cost_eur=actual.ai_eur,
                transcript=result.transcript,
                call_language=result.call_language or row.get("call_language") or "de",
                llm_model=result.llm_model or row.get("llm_model") or "",
                llm_model_source=result.llm_model_source or row.get("llm_model_source") or "",
                summary=None,
                counts_toward_caps=True,
            )
            LOG.info(
                "call finished id=%s outcome=%s duration_s=%.1f model=%s",
                call_id,
                result.outcome,
                duration,
                result.llm_model or row.get("llm_model") or "",
            )
        except Exception:
            LOG.exception("call failed id=%s", call_id)
            upsert_call(
                self.settings,
                call_id,
                status="finalized",
                outcome="failed",
                ended_at=self.now().isoformat(),
                summary=None,
                counts_toward_caps=True,
            )
        finally:
            active["done"].set()

    async def _wait(
        self,
        active: dict[str, Any],
        progress: ProgressFn | None,
        wait_seconds: float | None,
        max_minutes: int,
    ) -> dict[str, Any]:
        limit = float(max_minutes * 60 if wait_seconds is None else wait_seconds)
        elapsed = 0.0

        async def report(current: float, message: str) -> None:
            if progress is not None:
                await progress(current, limit, message)

        await report(0, "queued")
        task: asyncio.Task[Any] = active["task"]
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(active["done"].wait()), timeout=self.keepalive_s)
            except TimeoutError:
                elapsed += self.keepalive_s
                await report(min(elapsed, limit), "in_call")
                if elapsed >= limit:
                    break
                continue
            break
        await report(limit if task.done() else elapsed, "finalized" if task.done() else "wait_timeout")
        return self.get_call(active["call_id"])

    async def hangup(self, call_id: str) -> dict[str, Any]:
        row = get_stored_call(self.settings, call_id)
        if row is None:
            raise CallError("not_found", f"No call {call_id}.")
        if row.get("status") == "finalized":
            return {"status": row["status"], "outcome": row.get("outcome")}
        task = None
        async with self._lock:
            active = self._active
            if active and active["call_id"] == call_id:
                active["shutdown"].set()
                task = active.get("task")
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except TimeoutError:
                upsert_call(
                    self.settings,
                    call_id,
                    status="finalized",
                    outcome="hung_up_by_agent",
                    ended_at=self.now().isoformat(),
                )
        fresh = get_stored_call(self.settings, call_id) or row
        self._audit({"event": "hangup_call", "call_id": call_id, "status": fresh.get("status")})
        return {"status": fresh.get("status"), "outcome": fresh.get("outcome")}
