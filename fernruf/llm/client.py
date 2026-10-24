from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Awaitable
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from fernruf.llm.config import LlmEndpoint
from fernruf.llm.tools import CALL_TOOLS
from fernruf.settings import Settings

LOG = logging.getLogger("fernruf.llm")

ToolHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class LlmStreamResult:
    assistant_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    first_token_ms: float | None = None


class MockStreamingChatClient:
    """Deterministic LLM for CI when no API key is configured."""

    def __init__(self, *, endpoint: LlmEndpoint, task: str, language: str = "de") -> None:
        self.endpoint = endpoint
        self.task = task
        self.language = (language or "de").strip().lower() or "de"
        self._turn = 0

    def _script(self) -> tuple[str, str]:
        if self.language == "en":
            return (
                "Thanks for your time. I have noted that. Is there anything else important?",
                "Thank you. Goodbye.",
            )
        if self.language == "zh":
            return (
                "谢谢你的时间。我已经记下了。还有什么重要的吗？",
                "谢谢。再见。",
            )
        return (
            "Danke für Ihre Zeit. Ich habe alles notiert. Gibt es noch etwas Wichtiges?",
            "Vielen Dank, auf Wiederhören.",
        )

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_tool: ToolHandler | None = None,
    ) -> LlmStreamResult:
        self._turn += 1
        result = LlmStreamResult(first_token_ms=0.0)
        first, later = self._script()
        if self._turn == 1:
            text = first
        else:
            text = later
            if on_tool:
                await on_tool("end_call", {"reason": "mock_task_complete"})
            result.tool_calls.append({"name": "end_call", "arguments": {"reason": "mock"}})
        result.assistant_text = text
        if on_token:
            for word in text.split():
                await on_token(word + " ")
        return result


class StreamingChatClient:
    def __init__(self, *, endpoint: LlmEndpoint) -> None:
        self.endpoint = endpoint
        self._client = AsyncOpenAI(
            api_key=endpoint.api_key,
            base_url=endpoint.base_url,
            default_headers=endpoint.default_headers or None,
        )

    @classmethod
    def for_call(
        cls,
        settings: Settings,
        endpoint: LlmEndpoint,
    ) -> StreamingChatClient | MockStreamingChatClient:
        if settings.llm_mock or not endpoint.api_key:
            LOG.info("LLM mock mode (provider=%s model=%s)", endpoint.provider, endpoint.model)
            return MockStreamingChatClient(endpoint=endpoint, task="")
        return cls(endpoint=endpoint)

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_tool: ToolHandler | None = None,
    ) -> LlmStreamResult:
        ep = self.endpoint
        t0 = time.monotonic()
        first_token: float | None = None
        result = LlmStreamResult()
        tool_acc: dict[int, dict[str, Any]] = {}

        stream = await self._client.chat.completions.create(
            model=ep.model,
            messages=messages,
            tools=CALL_TOOLS,
            stream=True,
            temperature=0.4,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                if first_token is None:
                    first_token = (time.monotonic() - t0) * 1000
                result.assistant_text += delta.content
                if on_token:
                    await on_token(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    entry = tool_acc.setdefault(
                        idx, {"id": tc.id, "name": "", "arguments": ""}
                    )
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        result.first_token_ms = first_token
        for entry in tool_acc.values():
            try:
                args = json.loads(entry["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result.tool_calls.append({"name": entry["name"], "arguments": args})
            if on_tool and entry["name"]:
                await on_tool(entry["name"], args)
        return result
