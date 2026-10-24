from __future__ import annotations


def validate_llm_model_id(value: str) -> str:
    model = value.strip()
    if not model:
        raise ValueError("llm_model must be a non-empty string")
    if len(model) > 200:
        raise ValueError("llm_model too long (max 200 characters)")
    if any(ch.isspace() for ch in model):
        raise ValueError("llm_model must not contain whitespace")
    return model
