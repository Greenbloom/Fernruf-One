from __future__ import annotations

from dataclasses import dataclass

from fernruf.llm.extra import LlmReasoningMode


@dataclass(frozen=True)
class LlmPreflightResult:
    reasoning_override: LlmReasoningMode | None = None
    model_override: str | None = None
