from __future__ import annotations

import time
from typing import Any

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import LLMService

from fernruf.llm.config import pipecat_openai_kwargs
from fernruf.llm.extra import LlmReasoningMode, openrouter_llm_settings_extra
from fernruf.llm.stream_liveness import chunk_has_llm_liveness
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.services.fernruf_openai_llm import FernrufOpenAILLMService


def create_fernruf_llm_service(
    settings: Settings,
    *,
    model: str,
    provider: str,
    system_instruction: str,
    reasoning_override: LlmReasoningMode | None = None,
    session: M2CallSession | None = None,
) -> FernrufOpenAILLMService:
    llm_kwargs = pipecat_openai_kwargs(settings, model=model)
    llm_kwargs.pop("model", None)
    llm_extra = openrouter_llm_settings_extra(
        settings,
        provider=provider,
        reasoning_override=reasoning_override,
    )
    llm = FernrufOpenAILLMService(
        **llm_kwargs,
        call_settings=settings,
        call_session=session,
        settings=FernrufOpenAILLMService.Settings(
            model=model,
            temperature=0.4,
            system_instruction=system_instruction,
            extra=llm_extra,
        ),
    )
    llm._primary_model = model
    return llm


async def streaming_preflight_completion(
    llm: LLMService,
    context: LLMContext,
) -> float:
    """One streaming completion; returns milliseconds until first liveness delta."""
    assert isinstance(llm, FernrufOpenAILLMService)
    t0 = time.monotonic()
    stream = await llm.get_chat_completions(context)
    first_ms: float | None = None
    try:
        async for chunk in stream:
            if chunk_has_llm_liveness(chunk):
                first_ms = (time.monotonic() - t0) * 1000
                break
    finally:
        if hasattr(stream, "close"):
            await stream.close()
        elif hasattr(stream, "aclose"):
            await stream.aclose()
    return first_ms if first_ms is not None else (time.monotonic() - t0) * 1000
