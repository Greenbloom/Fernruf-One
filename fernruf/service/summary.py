"""Post-call summary for a completed call.

The model returns outcome, key facts, follow-ups, and consent. Key facts are
the answers to the task's questions. When the model is missing or its reply
cannot be used, the result is marked degraded instead of copying transcript
lines into the facts.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.summary")


def _consent(lines: list[dict[str, Any]]) -> str:
    asked = False
    for line in lines:
        text = str(line.get("text") or "")
        folded = text.casefold()
        speaker = line.get("speaker")
        if speaker == "agent" and (
            "aufzeichnen" in folded or "record this call" in folded or "录" in text
        ):
            asked = True
            continue
        if not asked or speaker != "callee":
            continue
        if any(word in folded for word in ("nein", "no", "nicht", "don't", "do not")):
            return "refused"
        if any(word in folded for word in ("ja", "yes", "klar", "gerne", "ok", "sure", "可以")):
            return "granted"
        return "unclear"
    return "not_asked"


def _degraded(transcript: list[dict[str, Any]], *, outcome: str, reason: str) -> dict[str, Any]:
    """A summary that did not answer the task. Facts stay empty on purpose."""
    LOG.warning("call summary degraded: %s", reason)
    lines = [line for line in transcript if isinstance(line, dict)]
    return {
        "outcome": outcome,
        "key_facts": [],
        "follow_ups": [],
        "consent": _consent(lines),
        "one_line": "",
        "task_status": "incomplete",
        "degraded": True,
    }


def _json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _unique_texts(items) -> list[str]:
    """One entry per distinct fact. The model repeats an unanswered question."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def _parse_summary(raw: str, *, outcome: str) -> dict[str, Any] | None:
    data = _json_object(raw)
    if data is None:
        return None
    facts = data.get("key_facts")
    follow = data.get("follow_ups")
    if isinstance(facts, str):
        facts = [facts]
    if follow is None:
        follow = []
    if not isinstance(facts, list) or not isinstance(follow, list):
        return None
    consent = str(data.get("consent") or "not_asked")
    if consent not in ("granted", "refused", "unclear", "not_asked"):
        consent = "unclear"
    task_status = str(data.get("task_status") or "completed")
    return {
        "outcome": str(data.get("outcome") or outcome),
        "key_facts": _unique_texts(str(item) for item in facts),
        "follow_ups": [str(item) for item in follow if str(item).strip()],
        "consent": consent,
        "one_line": str(data.get("one_line") or ""),
        "task_status": task_status,
    }


def _prompt(transcript: list[dict[str, Any]], task: str) -> str:
    lines = []
    for line in transcript:
        if not isinstance(line, dict):
            continue
        speaker = line.get("speaker")
        text = str(line.get("text") or "").strip()
        if speaker in ("agent", "callee") and text:
            lines.append(f"{speaker}: {text}")
    spoken = "\n".join(lines) if lines else "(no transcript)"
    return (
        "Summarize this completed phone call as JSON with keys "
        "outcome, key_facts, follow_ups, consent, one_line, task_status. "
        "key_facts is a list with one answer for each question in the task, "
        "in the task's language. Do not repeat the same key_fact. "
        "one_line is one sentence that states those "
        "answers. Do not copy filler, laughter, or goodbye lines. "
        "consent is granted, refused, or not_asked. "
        "Use only what was said. Do not include phone numbers.\n\n"
        f"Task:\n{task.strip() or '(none)'}\n\n"
        f"Transcript:\n{spoken}"
    )


async def _complete_with_llm(settings: Settings, prompt: str) -> str:
    from fernruf.llm.config import resolve_llm_endpoint

    ep = resolve_llm_endpoint(settings)
    url = ep.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {ep.api_key}", **ep.default_headers}
    payload = {
        "model": ep.model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Reply with one JSON object and no other text.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    return _message_text(message)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        content = "".join(parts)
    text = content if isinstance(content, str) else ""
    if text.strip():
        return text
    for key in ("reasoning", "reasoning_content"):
        extra = message.get(key)
        if isinstance(extra, str) and extra.strip():
            return extra
    return ""


async def summarize_completed_call(
    *,
    settings: Settings,
    transcript: list[dict[str, Any]],
    task: str,
    outcome: str,
    complete=None,
) -> dict[str, Any]:
    """Return the structured summary, or a degraded one when the model cannot."""
    prompt = _prompt(transcript, task)
    raw = ""
    if complete is not None:
        try:
            raw = await complete(prompt)
        except Exception as exc:
            return _degraded(transcript, outcome=outcome, reason=f"model call failed: {exc}")
    elif settings.llm_mock or not (settings.llm_api_key_resolved or "").strip():
        return _degraded(transcript, outcome=outcome, reason="no summary model configured")
    else:
        try:
            raw = await _complete_with_llm(settings, prompt)
        except Exception as exc:
            return _degraded(transcript, outcome=outcome, reason=f"model call failed: {exc}")
    parsed = _parse_summary(raw, outcome=outcome) if raw else None
    if parsed is not None:
        return parsed
    return _degraded(transcript, outcome=outcome, reason="model reply was not a summary")
