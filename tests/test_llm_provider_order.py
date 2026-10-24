from __future__ import annotations

from fernruf.llm.extra import openrouter_llm_settings_extra
from fernruf.settings import Settings


def test_openrouter_provider_order_from_env_list() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider_order="DeepInfra,Together",
    )
    extra = openrouter_llm_settings_extra(settings, provider="openrouter")
    assert extra["extra_body"]["provider"] == {
        "order": ["DeepInfra", "Together"],
        "allow_fallbacks": True,
    }
