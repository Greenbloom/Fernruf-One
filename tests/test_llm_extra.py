from __future__ import annotations

import pytest

from fernruf.llm.extra import normalize_llm_reasoning, openrouter_llm_settings_extra
from fernruf.llm.service_factory import create_fernruf_llm_service
from fernruf.settings import Settings


def test_openrouter_reasoning_in_extra_body_not_create_kwarg() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="https://openrouter.ai/api/v1",
        llm_reasoning="low",
    )
    llm = create_fernruf_llm_service(
        settings,
        model="example/model-b",
        provider="openrouter",
        system_instruction="Test system",
    )
    params = llm.build_chat_completion_params(
        {"messages": [{"role": "user", "content": "ping"}]}
    )
    assert "reasoning" not in params
    assert params["extra_body"]["reasoning"] == {"effort": "low"}
    assert params["stream"] is True


def test_openrouter_reasoning_none_omits_field() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="https://openrouter.ai/api/v1",
        llm_reasoning="none",
    )
    extra = openrouter_llm_settings_extra(settings, provider="openrouter")
    assert extra == {}


def test_non_openrouter_has_no_reasoning_extra() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="https://llm.example.com/v1",
        llm_reasoning="low",
    )
    assert openrouter_llm_settings_extra(settings, provider="openai_compatible") == {}


def test_reasoning_mode_off() -> None:
    assert normalize_llm_reasoning("off") == "off"
    extra = openrouter_llm_settings_extra(
        Settings(_env_file=None, llm_reasoning="off"),
        provider="openrouter",
    )
    assert extra["extra_body"]["reasoning"] == {"enabled": False}


def test_invalid_reasoning_rejected() -> None:
    with pytest.raises(ValueError, match="FERNRUF_LLM_REASONING"):
        normalize_llm_reasoning("high")
