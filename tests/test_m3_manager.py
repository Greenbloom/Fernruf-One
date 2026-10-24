"""Call manager on the simulated path. No SIP."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fernruf.quiet_hours import BERLIN
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallError, CallManager
from fernruf.service.records import upsert_call
from fernruf.service.runner import CallJob, CallRunResult
from tests.m3_support import service_settings

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
TASK = {"goal": "Frag, ob Dienstag passt.", "questions": ["Passt Dienstag?"]}


def _manager(tmp_path, *, line_up: bool = True, runner=None, keepalive_s: float = 5.0, **overrides):
    settings, _token = service_settings(tmp_path, **overrides)
    return CallManager(
        settings,
        line_health=StaticLineHealth(line_up),
        runner=runner,
        now=lambda: NOON,
        keepalive_s=keepalive_s,
    ), settings


def test_simulated_call_persists_transcript_cost_and_disclosure(tmp_path) -> None:
    manager, _settings = _manager(tmp_path)

    async def run():
        queued = await manager.place(to="+493023125123", task=TASK, model="openai/gpt-test")
        assert queued["status"] == "queued"
        assert queued["policy_checks"]["allowed"] is True
        for _ in range(50):
            view = manager.get_call(queued["call_id"])
            if view["status"] == "finalized":
                return view
            await asyncio.sleep(0.05)
        raise AssertionError("call did not finish")

    view = asyncio.run(run())
    assert view["outcome"] == "completed"
    assert view["to"] == "+493023125123"
    assert view["started_at"]
    assert view["answered_at"]
    assert view["ended_at"]
    assert view["duration_s"] >= 0
    assert view["cost_estimate_eur"] >= 0
    assert view["summary"] is None
    assert view["llm_model"] == "openai/gpt-test"
    assert view["llm_model_source"] == "override"
    lines = view["transcript"]["lines"]
    assert view["transcript"]["complete"] is True
    assert lines[0]["speaker"] == "agent"
    assert "KI-Assistent" in lines[0]["text"]
    assert view["last_lines"] == lines[-10:]
    assert view["call_language"] == "de"


def test_second_call_is_rejected_while_one_is_active(tmp_path) -> None:
    started = asyncio.Event()

    async def blocker(job: CallJob) -> CallRunResult:
        started.set()
        await job.shutdown.wait()
        return CallRunResult(outcome="hung_up_by_agent", answered=True, transcript=[])

    manager, _settings = _manager(tmp_path, runner=blocker)

    async def run():
        first = await manager.place(to="+493023125123", task=TASK)
        await started.wait()
        try:
            await manager.place(to="+493023125123", task=TASK)
            raise AssertionError("second call should have been rejected")
        except CallError as exc:
            assert exc.code == "concurrent_call"
        hung = await manager.hangup(first["call_id"])
        assert hung["outcome"] == "hung_up_by_agent"
        return first["call_id"]

    call_id = asyncio.run(run())
    assert manager.get_call(call_id)["status"] == "finalized"


def test_place_and_wait_emits_keepalive_progress(tmp_path) -> None:
    async def slow(job: CallJob) -> CallRunResult:
        for _ in range(4):
            if job.shutdown.is_set():
                break
            await asyncio.sleep(0.03)
        return CallRunResult(
            outcome="completed",
            answered=True,
            transcript=[{"t_ms": 0, "speaker": "agent", "text": "Hallo"}],
            call_language="de",
            llm_model="openrouter/test-model",
            llm_model_source="env",
        )

    manager, _settings = _manager(tmp_path, runner=slow, keepalive_s=0.02)
    seen: list[str] = []

    async def progress(current: float, total: float, message: str) -> None:
        seen.append(message)

    async def run():
        return await manager.place(
            to="+493023125123",
            task=TASK,
            wait=True,
            progress=progress,
        )

    view = asyncio.run(run())
    assert view["status"] == "finalized"
    assert seen[0] == "queued"
    assert "in_call" in seen
    assert seen[-1] == "finalized"


def test_daily_cap_is_enforced_from_stored_calls(tmp_path) -> None:
    manager, settings = _manager(tmp_path, FERNRUF_CALLS_PER_DAY=1)
    upsert_call(
        settings,
        "earlier",
        to="+493023125123",
        status="finalized",
        outcome="completed",
        started_at=NOON.isoformat(),
        ended_at=NOON.isoformat(),
        duration_s=30,
        cost_estimate_eur=0.01,
        mobile_cost_eur=0,
        reserved_minutes=1,
        counts_toward_caps=True,
        transcript=[],
        summary=None,
    )

    async def run():
        try:
            await manager.place(to="+493023125123", task=TASK)
        except CallError as exc:
            return exc.code
        return "allowed"

    assert asyncio.run(run()) == "daily_calls"


def test_configured_model_is_used_when_place_omits_one(tmp_path) -> None:
    manager, _settings = _manager(tmp_path, FERNRUF_LLM_MODEL="openrouter/other-model")

    async def run():
        queued = await manager.place(to="+493023125123", task=TASK)
        for _ in range(50):
            view = manager.get_call(queued["call_id"])
            if view["status"] == "finalized":
                return view
            await asyncio.sleep(0.05)
        raise AssertionError("call did not finish")

    view = asyncio.run(run())
    assert view["llm_model"] == "openrouter/other-model"
    assert view["llm_model_source"] in ("override", "saved", "env", "default")


VROOM_TASK = {
    "goal": (
        "Tell Alex that the ExampleApp test release v1.0.0 is ready, "
        "then end the call."
    ),
    "context": (
        "Alex asked to be called when the release was ready. "
        "The download link is already in his their team chat."
    ),
    "must_say": [
        "The ExampleApp test release v1.0.0",
        "The download link is in your chat with Example Bot.",
    ],
    "must_not": ["Do not agree to anything or make commitments."],
    "decision_limits": "None. This is a notification only.",
    "fallback": "If voicemail, leave a one-sentence message and hang up.",
}


def test_english_language_speaks_english_disclosure_not_saved_opening_line(tmp_path) -> None:
    """A saved German opening_line must not be the disclosure when language is en."""
    from fernruf.call_settings.store import update_call_settings
    from fernruf.principal import format_phrase_templates

    manager, settings = _manager(tmp_path)
    saved_german = format_phrase_templates("de", settings.require_principal())["ai_notice"]
    update_call_settings(
        {
            "opening_line": saved_german,
            "default_language": "de",
        },
        settings=settings,
    )
    view = asyncio.run(
        manager.place(
            to="+493023125123",
            task=VROOM_TASK,
            language="en",
            max_minutes=2,
            record=False,
            wait=True,
        )
    )
    opener = view["transcript"]["lines"][0]["text"]
    assert view["call_language"] == "en"
    assert opener == format_phrase_templates("en", settings.require_principal())["ai_notice"]
    assert "KI-Assistent" not in opener


def test_explicit_language_is_spoken_on_the_simulated_path(tmp_path) -> None:
    """Empty Jev credentials would otherwise leave the call in German."""
    manager, _settings = _manager(tmp_path)

    view = asyncio.run(
        manager.place(
            to="+493023125123",
            task=VROOM_TASK,
            language="en",
            max_minutes=2,
            record=False,
            wait=True,
        )
    )
    assert view["call_language"] == "en"
    opener = view["transcript"]["lines"][0]
    assert opener["speaker"] == "agent"
    assert "AI assistant" in opener["text"]
    assert "KI-Assistent" not in opener["text"]


def test_place_keeps_language_and_detection_text_on_the_job(tmp_path) -> None:
    import httpx

    from fernruf.call_settings.store import update_call_settings
    from fernruf.language import prepare_call_settings

    seen: dict[str, object] = {}

    async def runner(job: CallJob) -> CallRunResult:
        hits = {"n": 0}

        def would_say_german(_request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            return httpx.Response(
                200,
                json={"answers": {"language": {"choice": "de", "confidence": 0.99}}},
            )

        prepared = await prepare_call_settings(
            job.settings,
            job.task_text,
            language=job.language,
            detection_text=job.detection_text,
            transport=httpx.MockTransport(would_say_german),
        )
        seen["language"] = job.language
        seen["detection"] = job.detection_text
        seen["task"] = job.task_text
        seen["hits"] = hits["n"]
        seen["spoken"] = prepared.call_language
        return CallRunResult(
            outcome="completed",
            answered=True,
            transcript=[{"t_ms": 0, "speaker": "agent", "text": prepared.disclosure_prefix}],
            call_language=prepared.call_language,
        )

    manager, settings = _manager(tmp_path, runner=runner)
    update_call_settings({"default_language": "de"}, settings=settings)
    view = asyncio.run(
        manager.place(to="+493023125123", task=VROOM_TASK, language="EN", wait=True)
    )
    assert seen["language"] == "en"
    assert seen["hits"] == 0
    assert seen["spoken"] == "en"
    detection = str(seen["detection"])
    assert "Example Bot" in detection
    assert "ExampleApp test release v1.0.0" in detection
    assert "Muss sagen" not in detection
    assert "Ziel:" not in detection
    assert "voicemail" not in detection
    assert "Ziel:" in str(seen["task"])
    assert view["call_language"] == "en"


def test_omitted_language_detects_from_task_text(tmp_path) -> None:
    import httpx

    from fernruf.call_settings.store import update_call_settings
    from fernruf.language import prepare_call_settings
    from fernruf.mood import reset_mood_notices

    reset_mood_notices()

    async def runner(job: CallJob) -> CallRunResult:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["state"] = json.loads(request.content.decode())["state"]
            return httpx.Response(
                200,
                json={"answers": {"language": {"choice": "en", "confidence": 0.99}}},
            )

        prepared = await prepare_call_settings(
            job.settings,
            job.task_text,
            language=job.language,
            detection_text=job.detection_text,
            transport=httpx.MockTransport(handler),
        )
        assert job.language is None
        assert seen["state"]
        assert "Muss sagen" not in seen["state"]
        assert "Example Bot" in seen["state"]
        assert "fr" not in seen["state"]
        return CallRunResult(
            outcome="completed",
            answered=True,
            transcript=[],
            call_language=prepared.call_language,
        )

    manager, settings = _manager(
        tmp_path,
        runner=runner,
        TYPESAFE_API_KEY="test-token-value",
        FERNRUF_TYPESAFE_ENABLED="true",
    )
    update_call_settings({"default_language": "fr"}, settings=settings)
    view = asyncio.run(manager.place(to="+493023125123", task=VROOM_TASK, wait=True))
    assert view["call_language"] == "en"
