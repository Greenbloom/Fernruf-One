from __future__ import annotations

from typing import Any, Literal

from fernruf.settings import Settings

LlmReasoningMode = Literal["low", "off", "none"]


def normalize_llm_reasoning(raw: str) -> LlmReasoningMode:
    mode = (raw or "low").strip().lower()
    if mode not in ("low", "off", "none"):
        raise ValueError(f"FERNRUF_LLM_REASONING must be low, off, or none (got {raw!r})")
    return mode  # type: ignore[return-value]


def openrouter_llm_settings_extra(
    settings: Settings,
    *,
    provider: str,
    reasoning_override: LlmReasoningMode | None = None,
) -> dict[str, Any]:
    """Pipecat ``Settings.extra`` entries (must use ``extra_body``, not bare ``reasoning`` kwargs)."""
    if provider != "openrouter":
        return {}
    mode = normalize_llm_reasoning(
        reasoning_override if reasoning_override is not None else settings.llm_reasoning
    )
    extra_body: dict[str, Any] = {}
    if mode == "off":
        extra_body["reasoning"] = {"enabled": False}
    elif mode == "low":
        extra_body["reasoning"] = {"effort": "low"}
    order_raw = (settings.llm_provider_order or "").strip()
    if order_raw:
        order = [p.strip() for p in order_raw.split(",") if p.strip()]
        if order:
            extra_body["provider"] = {
                "order": order,
                "allow_fallbacks": True,
            }
    else:
        provider_slug = (settings.llm_provider or "").strip()
        if provider_slug:
            extra_body["provider"] = {
                "order": [provider_slug],
                "allow_fallbacks": True,
            }
    if not extra_body:
        return {}
    return {"extra_body": extra_body}
