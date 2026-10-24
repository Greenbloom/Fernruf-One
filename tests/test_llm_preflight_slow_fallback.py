from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from fernruf.llm.preflight import preflight_llm
from fernruf.settings import Settings


def test_preflight_slow_first_token_switches_to_fallback() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="example/model-a",
        llm_fallback_model="example/model-b",
        llm_preflight_slow_first_token_s=3.0,
    )
    calls: list[str] = []

    async def _fake_once(*_args, model: str, **_kwargs):  # noqa: ANN001
        calls.append(model)
        if model == "example/model-a":
            return 10_200.0
        return 120.0

    with patch(
        "fernruf.llm.preflight.resolve_llm_endpoint_for_call",
        return_value=(MagicMock(model="example/model-a", provider="openrouter"), MagicMock()),
    ), patch("fernruf.llm.preflight._streaming_preflight_once", side_effect=_fake_once):
        result = asyncio.run(
            preflight_llm(
                settings,
                system_instruction="sys",
                tools=[],
            )
        )
    assert result.model_override == "example/model-b"
    assert calls == ["example/model-a", "example/model-b"]
