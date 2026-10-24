from fernruf.llm.config import resolve_llm_endpoint
from fernruf.settings import Settings


def test_openrouter_defaults():
    s = Settings(
        FERNRUF_LLM_BASE_URL="https://openrouter.ai/api/v1",
        OPENROUTER_API_KEY="sk-or-test",
        FERNRUF_LLM_MODEL="example/model-b",
    )
    ep = resolve_llm_endpoint(s)
    assert ep.provider == "openrouter"
    assert ep.api_key == "sk-or-test"
    assert ep.model == "example/model-b"
    assert ep.default_headers.get("X-Title") == "fernruf"


def test_openai_compatible_base_url():
    s = Settings(
        FERNRUF_LLM_BASE_URL="https://llm.example.com/v1",
        FERNRUF_LLM_API_KEY="sk-test",
        FERNRUF_LLM_MODEL="vendor/example-model",
    )
    ep = resolve_llm_endpoint(s)
    assert ep.provider == "openai_compatible"
    assert ep.api_key == "sk-test"
