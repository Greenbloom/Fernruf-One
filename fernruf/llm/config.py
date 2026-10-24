from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fernruf.settings import Settings


@dataclass(frozen=True)
class LlmEndpoint:
    base_url: str
    api_key: str
    model: str
    provider: str
    default_headers: dict[str, str]


def provider_label(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if "openrouter.ai" in host:
        return "openrouter"
    return "openai_compatible"


def resolve_llm_endpoint(settings: Settings) -> LlmEndpoint:
    base = settings.llm_base_url.rstrip("/")
    key = settings.llm_api_key_resolved
    headers: dict[str, str] = {}
    if settings.llm_http_referer:
        headers["HTTP-Referer"] = settings.llm_http_referer
    if settings.llm_x_title:
        headers["X-Title"] = settings.llm_x_title
    return LlmEndpoint(
        base_url=base,
        api_key=key,
        model=settings.llm_model,
        provider=provider_label(base),
        default_headers=headers,
    )


def pipecat_openai_kwargs(settings: Settings, *, model: str | None = None) -> dict:
    if model:
        ep = resolve_llm_endpoint(settings)
        ep = LlmEndpoint(
            base_url=ep.base_url,
            api_key=ep.api_key,
            model=model,
            provider=ep.provider,
            default_headers=ep.default_headers,
        )
    else:
        ep = resolve_llm_endpoint(settings)
    return {
        "api_key": ep.api_key or "mock-key",
        "base_url": ep.base_url,
        "model": ep.model,
        "default_headers": ep.default_headers or None,
    }
