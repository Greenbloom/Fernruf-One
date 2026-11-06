"""Post-call summary for a completed call.

The model returns outcome, key facts, follow-ups, and consent. When no model
is configured the same shape is filled from the transcript so a completed
call is never stored with a null summary.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.summary")

_KEYS = ("outcome", "key_facts", "follow_ups", "consent", "one_line", "task_status")


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


def summary_from_transcript(
    transcript: list[dict[str, Any]],
    *,
    outcome: str,
) -> dict[str, Any]:
    lines = [line for line in transcript if isinstance(line, dict)]
    facts = [
        str(line.get("text") or "").strip()
        for line in lines
        if line.get("speaker") == "callee" and str(line.get("text") or "").strip()
    ][:8]
    return {
        "outcome": outcome,
        "key_facts": facts,
        "follow_ups": [],
        "consent": _consent(lines),
        "one_line": (facts[-1] if facts else ""),
        "task_status": "completed" if outcome == "completed" else "incomplete",
    }


def _parse_summary(raw: str, *, outcome: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    facts = data.get("key_facts")
    follow = data.get("follow_ups")
    if not isinstance(facts, list) or not isinstance(follow, list):
        return None
    consent = str(data.get("consent") or "not_asked")
    if consent not in ("granted", "refused", "unclear", "not_asked"):
        consent = "unclear"
    task_status = str(data.get("task_status") or "completed")
    return {
        "outcome": str(data.get("outcome") or outcome),
        "key_facts": [str(item) for item in facts if str(item).strip()],
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
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    return content if isinstance(content, str) else ""


async def summarize_completed_call(
    *,
    settings: Settings,
    transcript: list[dict[str, Any]],
    task: str,
    outcome: str,
    complete=None,
) -> dict[str, Any]:
    """Return the structured summary. Always a dict for a completed call."""
    prompt = _prompt(transcript, task)
    raw = ""
    if complete is not None:
        raw = await complete(prompt)
    elif not settings.llm_mock and (settings.llm_api_key_resolved or "").strip():
        try:
            raw = await _complete_with_llm(settings, prompt)
        except Exception:
            LOG.warning("summary model failed; using the transcript")
            raw = ""
    parsed = _parse_summary(raw, outcome=outcome) if raw else None
    if parsed is not None:
        return parsed
    summary = summary_from_transcript(transcript, outcome=outcome)
    for key in _KEYS:
        summary.setdefault(key, "")
    return summary
