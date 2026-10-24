from __future__ import annotations

from typing import Any

from openai.types.chat import ChatCompletionChunk


def _delta_field(delta: Any, name: str) -> str | None:
    if delta is None:
        return None
    val = getattr(delta, name, None)
    if val is None and hasattr(delta, "model_extra") and delta.model_extra:
        val = delta.model_extra.get(name)
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, list) and val:
        return "1"
    if val is not None:
        return str(val)
    return None


def delta_has_llm_liveness(delta: Any) -> bool:
    if delta is None:
        return False
    if getattr(delta, "tool_calls", None):
        return True
    for name in ("content", "reasoning", "reasoning_content"):
        if _delta_field(delta, name):
            return True
    if _delta_field(delta, "reasoning_details"):
        return True
    extra = getattr(delta, "model_extra", None) or {}
    if isinstance(extra, dict) and extra.get("reasoning_details"):
        return True
    return False


def chunk_has_llm_liveness(chunk: ChatCompletionChunk) -> bool:
    if chunk.choices is None or not chunk.choices:
        return False
    choice = chunk.choices[0]
    if choice.delta and delta_has_llm_liveness(choice.delta):
        return True
    return False
