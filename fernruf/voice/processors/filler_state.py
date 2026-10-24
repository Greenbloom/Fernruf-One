from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class LlmFillerState:
    """Shared between STT-side scheduler and LLM-side cancel gate."""

    wait_task: asyncio.Task | None = None
    llm_text_seen: bool = False
