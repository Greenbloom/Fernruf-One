import asyncio
from pathlib import Path

import pytest

from fernruf.call_settings.model import validate_llm_model_id
from fernruf.call_settings.resolver import resolve_call_llm_model
from fernruf.call_settings.store import get_call_settings, update_call_settings
from fernruf.settings import Settings
from fernruf.voice.simulate import SimulateConversation


def test_validate_llm_model_id_rejects_empty():
    with pytest.raises(ValueError):
        validate_llm_model_id("   ")


def test_resolve_order_override_persisted_env(tmp_path: Path):
    db = tmp_path / "s.sqlite"
    s = Settings(
        FERNRUF_LLM_MODEL="env/default",
        FERNRUF_SETTINGS_DB=str(db),
    )
    update_call_settings({"llm_model": "example/model-b"}, settings=s)
    r = resolve_call_llm_model(s)
    assert r.source == "persisted"
    assert r.model == "example/model-b"
    r2 = resolve_call_llm_model(s, override="openrouter/other-model")
    assert r2.source == "override"
    assert r2.model == "openrouter/other-model"
    s2 = Settings(FERNRUF_LLM_MODEL="env-only", FERNRUF_SETTINGS_DB=str(tmp_path / "empty.sqlite"))
    r3 = resolve_call_llm_model(s2)
    assert r3.source == "env"
    assert r3.model == "env-only"


def test_persisted_model_used_on_next_simulated_call(tmp_path: Path):
    db = tmp_path / "s.sqlite"
    s = Settings(
        FERNRUF_LLM_MOCK=True,
        FERNRUF_VOICE_MOCK=True,
        FERNRUF_SETTINGS_DB=str(db),
        FERNRUF_LATENCY_LOG=str(tmp_path / "lat.jsonl"),
    )
    update_call_settings({"llm_model": "openrouter/example-test"}, settings=s)
    result = asyncio.run(SimulateConversation(s, "test").run())
    assert result.llm_model == "openrouter/example-test"
    assert result.llm_model_source == "persisted"
