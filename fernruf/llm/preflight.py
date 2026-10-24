from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import APIStatusError
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.http import TIMEOUT_EXCEPTIONS

from fernruf.call_settings.resolver import resolve_llm_endpoint_for_call
from fernruf.llm.extra import LlmReasoningMode, normalize_llm_reasoning
from fernruf.llm.preflight_result import LlmPreflightResult
from fernruf.llm.service_factory import create_fernruf_llm_service, streaming_preflight_completion
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.llm.preflight")


class LlmPreflightError(RuntimeError):
    """Configured LLM model rejected before dial."""


def _format_api_error(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        try:
            body = exc.response.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
        except Exception:
            pass
        return (getattr(exc, "message", None) or str(exc))[:500]
    return str(exc)[:500]


def is_reasoning_mandatory_openrouter_error(exc: Exception) -> bool:
    """OpenRouter 400 when ``reasoning.enabled=false`` is rejected for the model."""
    if not isinstance(exc, APIStatusError) or exc.status_code != 400:
        return False
    msg = _format_api_error(exc).lower()
    return "reasoning is mandatory" in msg and "cannot be disabled" in msg


async def _streaming_preflight_once(
    settings: Settings,
    *,
    model: str,
    system_instruction: str,
    tools: list[Any],
    reasoning_override: LlmReasoningMode | None,
    timeout_s: float,
) -> float:
    ep, _resolution = resolve_llm_endpoint_for_call(settings, call_model_override=model)
    llm = create_fernruf_llm_service(
        settings,
        model=ep.model,
        provider=ep.provider,
        system_instruction=system_instruction,
        reasoning_override=reasoning_override,
    )
    context = LLMContext(
        messages=[{"role": "user", "content": "ping"}],
        tools=tools,
    )
    return await asyncio.wait_for(
        streaming_preflight_completion(llm, context),
        timeout=timeout_s,
    )


async def preflight_llm(
    settings: Settings,
    *,
    model: str | None = None,
    system_instruction: str,
    tools: list[Any] | None = None,
    timeout_s: float = 25.0,
) -> LlmPreflightResult:
    """Streaming preflight via the same FernrufOpenAILLMService request builder as M2 calls."""
    if settings.llm_mock or settings.voice_mock:
        return LlmPreflightResult()

    ep, _resolution = resolve_llm_endpoint_for_call(settings, call_model_override=model)
    tool_list = tools or []
    configured = normalize_llm_reasoning(settings.llm_reasoning)
    reasoning_override: LlmReasoningMode | None = None
    slow_threshold_s = settings.llm_preflight_slow_first_token_s
    fallback = (settings.llm_fallback_model or "").strip()

    async def _run_and_maybe_fallback(
        run_model: str,
        reasoning: LlmReasoningMode | None,
    ) -> LlmPreflightResult:
        first_ms = await _streaming_preflight_once(
            settings,
            model=run_model,
            system_instruction=system_instruction,
            tools=tool_list,
            reasoning_override=reasoning,
            timeout_s=timeout_s,
        )
        first_s = first_ms / 1000.0
        if first_s > slow_threshold_s and fallback and fallback != run_model:
            LOG.warning(
                "preflight slow: %.1fs, using fallback model=%s (was %s)",
                first_s,
                fallback,
                run_model,
            )
            await _streaming_preflight_once(
                settings,
                model=fallback,
                system_instruction=system_instruction,
                tools=tool_list,
                reasoning_override=reasoning,
                timeout_s=timeout_s,
            )
            return LlmPreflightResult(
                reasoning_override=reasoning,
                model_override=fallback,
            )
        LOG.info("LLM preflight ok model=%s first_token=%.0fms", run_model, first_ms)
        return LlmPreflightResult(reasoning_override=reasoning)

    try:
        return await _run_and_maybe_fallback(ep.model, None)
    except TIMEOUT_EXCEPTIONS as exc:
        msg = f"LLM preflight timed out for model {ep.model}"
        LOG.error(msg)
        raise LlmPreflightError(msg) from exc
    except Exception as exc:
        if configured == "off" and is_reasoning_mandatory_openrouter_error(exc):
            detail = _format_api_error(exc)
            LOG.warning(
                "OpenRouter rejected reasoning.enabled=false for model %s (%s); "
                "retrying preflight with reasoning effort low for this call",
                ep.model,
                detail[:200],
            )
            try:
                reasoning_override = "low"
                return await _run_and_maybe_fallback(ep.model, "low")
            except TIMEOUT_EXCEPTIONS as retry_exc:
                msg = f"LLM preflight timed out for model {ep.model} (reasoning low retry)"
                LOG.error(msg)
                raise LlmPreflightError(msg) from retry_exc
            except Exception as retry_exc:
                detail = _format_api_error(retry_exc)
                msg = f"LLM preflight failed for model {ep.model}: {detail}"
                LOG.error(msg)
                raise LlmPreflightError(msg) from retry_exc

        detail = _format_api_error(exc)
        msg = f"LLM preflight failed for model {ep.model}: {detail}"
        LOG.error(msg)
        raise LlmPreflightError(msg) from exc
