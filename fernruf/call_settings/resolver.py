from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from fernruf.call_settings.model import validate_llm_model_id
from fernruf.call_settings.store import get_call_settings
from fernruf.llm.config import LlmEndpoint, provider_label
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.llm")

ModelSource = Literal["override", "persisted", "env"]


@dataclass(frozen=True)
class LlmModelResolution:
    model: str
    source: ModelSource


def resolve_call_llm_model(
    settings: Settings,
    *,
    override: str | None = None,
) -> LlmModelResolution:
    if override is not None and override.strip():
        model = validate_llm_model_id(override)
        return LlmModelResolution(model=model, source="override")

    persisted = get_call_settings(settings)
    if persisted.llm_model_user_set and persisted.llm_model.strip():
        model = validate_llm_model_id(persisted.llm_model)
        return LlmModelResolution(model=model, source="persisted")

    raw = (settings.llm_model or "").strip()
    if not raw:
        if settings.llm_mock:
            return LlmModelResolution(model="mock", source="env")
        raise ValueError("llm_model must be a non-empty string")
    model = validate_llm_model_id(raw)
    return LlmModelResolution(model=model, source="env")


def llm_model_missing_reason(settings: Settings) -> str | None:
    if settings.llm_mock:
        return None
    if (settings.llm_model or "").strip():
        return None
    persisted = get_call_settings(settings)
    if persisted.llm_model_user_set and persisted.llm_model.strip():
        return None
    return "Set FERNRUF_LLM_MODEL (or save llm_model via settings) before placing a real call."


def resolve_llm_endpoint_for_call(
    settings: Settings,
    *,
    call_model_override: str | None = None,
) -> tuple[LlmEndpoint, LlmModelResolution]:
    resolution = resolve_call_llm_model(settings, override=call_model_override)
    base = settings.llm_base_url.rstrip("/")
    key = settings.llm_api_key_resolved
    headers: dict[str, str] = {}
    if settings.llm_http_referer:
        headers["HTTP-Referer"] = settings.llm_http_referer
    if settings.llm_x_title:
        headers["X-Title"] = settings.llm_x_title
    provider = provider_label(base)
    ep = LlmEndpoint(
        base_url=base,
        api_key=key,
        model=resolution.model,
        provider=provider,
        default_headers=headers,
    )
    LOG.info(
        "call LLM resolved model=%s provider=%s source=%s",
        resolution.model,
        provider,
        resolution.source,
    )
    return ep, resolution
