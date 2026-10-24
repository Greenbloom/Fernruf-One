"""MCP tool contract through the SDK's in-process client."""

from __future__ import annotations

import asyncio

import pytest
from mcp.client.client import Client

from fernruf.call_settings.store import get_call_settings
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallManager
from fernruf.service.mcp_app import create_app
from fernruf.service.records import upsert_call
from tests.m3_support import service_settings, tool_payload

TOOL_NAMES = [
    "get_calling_guide",
    "get_balance",
    "get_call_settings",
    "update_call_settings",
    "place_call",
    "place_call_and_wait",
    "get_call",
    "get_call_transcript",
    "list_calls",
    "get_recording",
    "hangup_call",
    "connect_webhook",
]
TASK = {"goal": "Frag, ob Dienstag passt.", "questions": ["Passt Dienstag?"]}


def _app(tmp_path, **overrides):
    from datetime import datetime

    from fernruf.quiet_hours import BERLIN

    settings, token = service_settings(tmp_path, **overrides)
    posted: list[dict] = []

    async def poster(url: str, body: dict) -> bool:
        posted.append({"url": url, "body": body})
        return True

    manager = CallManager(
        settings,
        line_health=StaticLineHealth(True),
        now=lambda: datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN),
        keepalive_s=0.05,
    )
    app = create_app(settings, manager=manager, poster=poster, line_health=StaticLineHealth(True))
    return app, settings, token, posted


def test_tool_list_matches_expected_mcp_names(tmp_path) -> None:
    app, _settings, _token, _posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            listed = await client.list_tools()
            return [tool.name for tool in listed.tools]

    names = asyncio.run(run())
    assert names == TOOL_NAMES


def test_writes_require_the_guide_in_the_same_session(tmp_path) -> None:
    app, settings, _token, posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            denied = await client.call_tool(
                "place_call",
                {"to": "+493023125123", "task": TASK},
            )
            assert denied.is_error
            assert tool_payload(denied)["error_code"] == "guide_required"
            guide = await client.call_tool("get_calling_guide", {})
            assert not guide.is_error
            text = guide.content[0].text
            assert "Allowlist" in text
            assert "marketing" in text.lower()
            placed = await client.call_tool(
                "place_call",
                {"to": "+493023125123", "task": TASK, "model": "openai/gpt-test"},
            )
            assert not placed.is_error
            queued = tool_payload(placed)
            for _ in range(50):
                current = await client.call_tool("get_call", {"call_id": queued["call_id"]})
                view = tool_payload(current)
                if view["status"] == "finalized":
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("call did not finish")
            transcript = tool_payload(
                await client.call_tool("get_call_transcript", {"call_id": queued["call_id"]})
            )
            recording = tool_payload(
                await client.call_tool("get_recording", {"call_id": queued["call_id"]})
            )
            balance = tool_payload(await client.call_tool("get_balance", {}))
            listed = tool_payload(await client.call_tool("list_calls", {"limit": 5}))
            hooked = tool_payload(
                await client.call_tool(
                    "connect_webhook",
                    {"url": "https://example.com/hook"},
                )
            )
            return view, transcript, recording, balance, listed, hooked

    view, transcript, recording, balance, listed, hooked = asyncio.run(run())
    assert view["outcome"] == "completed"
    assert "KI-Assistent" in transcript["lines"][0]["text"]
    assert recording["status"] == "not_available"
    assert recording["url"] is None
    assert balance["calls_today_max"] == 10
    assert balance["monthly_budget_eur"] == 25
    assert listed[0]["call_id"] == view["call_id"]
    assert hooked["test_delivery"] == "ok"
    assert posted and posted[0]["body"]["event"] == "webhook.test"
    assert "transcript" not in posted[0]["body"]
    saved = get_call_settings(settings)
    assert saved.allowlist == ["+493023125123"]


def test_a_new_session_must_read_the_guide_again(tmp_path) -> None:
    app, _settings, _token, _posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as first:
            await first.call_tool("get_calling_guide", {})
            ok = await first.call_tool("hangup_call", {"call_id": "missing"})
            assert tool_payload(ok)["error_code"] == "not_found"
        async with Client(app.state.mcp, mode="legacy") as second:
            denied = await second.call_tool("hangup_call", {"call_id": "missing"})
            return tool_payload(denied)["error_code"]

    assert asyncio.run(run()) == "guide_required"


def test_mcp_cannot_change_opening_line(tmp_path) -> None:
    from fernruf.language import prepare_call_settings
    from fernruf.service.settings_update import SettingsUpdateError, apply_mcp_settings

    settings, _token = service_settings(tmp_path)
    fixed = "Hallo, hier ist ein KI-Assistent, der im Auftrag von Alex Example anruft."

    with pytest.raises(SettingsUpdateError) as exc:
        apply_mcp_settings(settings, {"opening_line": "This is not the fixed disclosure."})
    assert exc.value.code == "setting_requires_cli"

    prepared = asyncio.run(
        prepare_call_settings(settings, "Kurzer Test", language="de")
    )
    assert prepared.disclosure_prefix == fixed


def test_mcp_cannot_change_quiet_hours(tmp_path) -> None:
    from fernruf.service.settings_update import SettingsUpdateError, apply_mcp_settings

    settings, _token = service_settings(tmp_path)
    with pytest.raises(SettingsUpdateError) as exc:
        apply_mcp_settings(settings, {"quiet_hours": "08:00-08:01"})
    assert exc.value.code == "setting_requires_cli"


def test_mcp_cannot_widen_allowlist_or_raise_caps(tmp_path) -> None:
    app, settings, _token, _posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            await client.call_tool("get_calling_guide", {})
            allow = await client.call_tool(
                "update_call_settings",
                {"allowlist": ["+493023125123", "+493023125124"]},
            )
            budget = await client.call_tool(
                "update_call_settings",
                {"monthly_budget_eur": 999},
            )
            minutes = await client.call_tool(
                "update_call_settings",
                {"max_minutes": 30},
            )
            lowered = await client.call_tool(
                "update_call_settings",
                {"max_minutes": 5, "monthly_budget_eur": 8},
            )
            return (
                tool_payload(allow),
                tool_payload(budget),
                tool_payload(minutes),
                tool_payload(lowered),
            )

    allow, budget, minutes, lowered = asyncio.run(run())
    assert allow["error_code"] == "setting_requires_cli"
    assert budget["error_code"] == "above_ceiling"
    assert minutes["error_code"] == "above_ceiling"
    assert lowered["max_minutes"] == 5
    assert lowered["monthly_budget_eur"] == 8
    assert get_call_settings(settings).allowlist == ["+493023125123"]


def test_private_webhook_is_rejected(tmp_path) -> None:
    app, _settings, _token, posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            await client.call_tool("get_calling_guide", {})
            local = await client.call_tool(
                "connect_webhook",
                {"url": "https://127.0.0.1/hook"},
            )
            meta = await client.call_tool(
                "connect_webhook",
                {"url": "http://169.254.169.254/latest"},
            )
            return tool_payload(local), tool_payload(meta)

    local, meta = asyncio.run(run())
    assert local["error_code"] == "webhook_url_rejected"
    assert meta["error_code"] == "webhook_url_rejected"
    assert posted == []


def test_place_and_wait_reports_progress(tmp_path) -> None:
    app, _settings, _token, _posted = _app(tmp_path)
    events: list[tuple] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, total, message))

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            await client.call_tool("get_calling_guide", {})
            result = await client.call_tool(
                "place_call_and_wait",
                {"to": "+493023125123", "task": TASK},
                progress_callback=on_progress,
            )
            return tool_payload(result)

    view = asyncio.run(run())
    assert view["status"] == "finalized"
    assert view["outcome"] == "completed"
    assert events
    assert events[0][2] == "queued"


def test_marketing_call_is_a_tool_error(tmp_path) -> None:
    app, _settings, _token, _posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            await client.call_tool("get_calling_guide", {})
            result = await client.call_tool(
                "place_call",
                {"to": "+493023125123", "task": {"goal": "Kaltakquise für das Produkt."}},
            )
            return tool_payload(result)

    body = asyncio.run(run())
    assert body["error_code"] == "marketing_prohibited"


def test_seeded_spend_blocks_the_tool(tmp_path) -> None:
    from datetime import datetime

    from fernruf.quiet_hours import BERLIN

    app, settings, _token, _posted = _app(tmp_path, FERNRUF_CALLS_PER_DAY=1)
    noon = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
    upsert_call(
        settings,
        "spent",
        to="+493023125123",
        status="finalized",
        outcome="completed",
        started_at=noon.isoformat(),
        ended_at=noon.isoformat(),
        duration_s=60,
        cost_estimate_eur=0.2,
        mobile_cost_eur=0.2,
        counts_toward_caps=True,
        transcript=[],
        summary=None,
    )

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            await client.call_tool("get_calling_guide", {})
            result = await client.call_tool(
                "place_call",
                {"to": "+493023125123", "task": TASK},
            )
            return tool_payload(result)

    assert asyncio.run(run())["error_code"] == "daily_calls"


VROOM_TASK = {
    "goal": (
        "Tell Alex that the ExampleApp test release v1.0.0 is ready, "
        "then end the call."
    ),
    "context": "Alex asked to be called when the release was ready.",
    "must_say": [
        "The ExampleApp test release v1.0.0",
        "The download link is in your chat with Example Bot.",
    ],
    "must_not": ["Do not agree to anything or make commitments."],
    "decision_limits": "None. This is a notification only.",
    "fallback": "If voicemail, leave a one-sentence message and hang up.",
}


def test_language_argument_overrides_detection_through_mcp(tmp_path) -> None:
    app, _settings, _token, _posted = _app(tmp_path)

    async def run():
        async with Client(app.state.mcp, mode="legacy") as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            guide = await client.call_tool("get_calling_guide", {})
            placed = await client.call_tool(
                "place_call",
                {
                    "to": "+493023125123",
                    "language": "en",
                    "max_minutes": 2,
                    "record": False,
                    "task": VROOM_TASK,
                },
            )
            assert not placed.is_error
            queued = tool_payload(placed)
            for _ in range(50):
                current = await client.call_tool("get_call", {"call_id": queued["call_id"]})
                view = tool_payload(current)
                if view["status"] == "finalized":
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("call did not finish")
            return by_name, guide.content[0].text, view

    tools, guide, view = asyncio.run(run())
    assert "overrides detection" in (tools["place_call"].description or "")
    assert "overrides detection" in (tools["place_call_and_wait"].description or "")
    assert "default_language" in (tools["place_call"].description or "")
    assert "overrides detection" in guide
    assert "default_language" in guide
    assert "no language switch" not in guide
    assert view["call_language"] == "en"
    opener = view["transcript"]["lines"][0]
    assert opener["speaker"] == "agent"
    assert "AI assistant" in opener["text"]
