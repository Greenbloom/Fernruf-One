"""The call summary answers the task. A failed model does not dump the transcript."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from fernruf.service.summary import summarize_completed_call
from fernruf.settings import Settings

TASK = (
    "Ask whether Tuesday at 15:00 still works, and whether they want a reminder."
)
TRANSCRIPT = [
    {"t_ms": 0, "speaker": "agent", "text": "Does Tuesday at 15:00 still work?"},
    {"t_ms": 1000, "speaker": "callee", "text": "Mhm."},
    {"t_ms": 2000, "speaker": "agent", "text": "Should I send a reminder?"},
    {"t_ms": 3000, "speaker": "callee", "text": "Yes, Tuesday at 15:00 works. No reminder."},
    {"t_ms": 4000, "speaker": "callee", "text": "Goodbye."},
]


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, llm_mock=False, llm_api_key="test-token-value")
    base.update(overrides)
    return Settings(**base)


def test_summary_answers_the_task_questions() -> None:
    async def complete(prompt: str) -> str:
        assert "Tuesday at 15:00" in prompt
        assert "reminder" in prompt
        assert "Mhm." in prompt
        return json.dumps(
            {
                "outcome": "completed",
                "key_facts": [
                    "Tuesday at 15:00 still works.",
                    "They do not want a reminder.",
                ],
                "follow_ups": [],
                "consent": "not_asked",
                "one_line": "Tuesday at 15:00 still works, and they do not want a reminder.",
                "task_status": "completed",
            }
        )

    summary = asyncio.run(
        summarize_completed_call(
            settings=_settings(),
            transcript=TRANSCRIPT,
            task=TASK,
            outcome="completed",
            complete=complete,
        )
    )
    assert summary.get("degraded") is not True
    assert summary["key_facts"] == [
        "Tuesday at 15:00 still works.",
        "They do not want a reminder.",
    ]
    assert "Mhm." not in summary["key_facts"]
    assert summary["one_line"].startswith("Tuesday at 15:00")
    assert "Goodbye." != summary["one_line"]


def test_fenced_model_json_is_still_a_summary() -> None:
    async def complete(_prompt: str) -> str:
        return (
            "```json\n"
            + json.dumps(
                {
                    "outcome": "completed",
                    "key_facts": ["Tuesday at 15:00 still works."],
                    "one_line": "Tuesday at 15:00 still works.",
                }
            )
            + "\n```"
        )

    summary = asyncio.run(
        summarize_completed_call(
            settings=_settings(),
            transcript=TRANSCRIPT,
            task=TASK,
            outcome="completed",
            complete=complete,
        )
    )
    assert summary.get("degraded") is not True
    assert summary["key_facts"] == ["Tuesday at 15:00 still works."]
    assert summary["follow_ups"] == []


def test_failed_model_is_degraded_and_not_the_transcript(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def complete(_prompt: str) -> str:
        raise RuntimeError("upstream timed out")

    caplog.set_level(logging.WARNING)
    summary = asyncio.run(
        summarize_completed_call(
            settings=_settings(),
            transcript=TRANSCRIPT,
            task=TASK,
            outcome="completed",
            complete=complete,
        )
    )
    assert summary["degraded"] is True
    assert summary["key_facts"] == []
    assert summary["one_line"] == ""
    assert "Mhm." not in json.dumps(summary)
    assert any("upstream timed out" in record.message for record in caplog.records)


def test_unparsed_model_text_is_degraded(caplog: pytest.LogCaptureFixture) -> None:
    async def complete(_prompt: str) -> str:
        return "Mhm. Goodbye."

    caplog.set_level(logging.WARNING)
    summary = asyncio.run(
        summarize_completed_call(
            settings=_settings(),
            transcript=TRANSCRIPT,
            task=TASK,
            outcome="completed",
            complete=complete,
        )
    )
    assert summary["degraded"] is True
    assert summary["key_facts"] == []
    assert summary["one_line"] == ""
    assert any("not a summary" in record.message for record in caplog.records)
