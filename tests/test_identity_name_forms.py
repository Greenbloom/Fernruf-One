"""Self-introduction patterns for every principal_tokens() form (plain and titled)."""

from __future__ import annotations

import pytest

from fernruf.principal import format_phrase_templates, principal_tokens
from fernruf.voice.identity import scrub_identity_text


@pytest.mark.parametrize("principal", ("Jordan Lee", "Dr. Erika Muster"))
def test_self_introduction_name_forms(principal: str) -> None:
    tokens = principal_tokens(principal)
    assert len(tokens) >= 2
    full = tokens[0]
    without_title = tokens[1] if len(tokens) > 1 else full
    first = tokens[-1]
    en = format_phrase_templates("en", principal)["identity_line"]
    zh = format_phrase_templates("zh", principal)["identity_line"]

    positives: list[tuple[str, str]] = [
        (f"I'm {first}.", en),
        (f"I am {first}.", en),
        (f"This is {first}.", en),
        (f"This is {first} speaking.", en),
        (f"My name is {first}.", en),
        (f"I'm {without_title}.", en),
        (f"I am {without_title}.", en),
        (f"This is {without_title}.", en),
        (f"My name is {without_title}.", en),
        (f"I'm {full}.", en),
        (f"I am {full}.", en),
        (f"This is {full}.", en),
        (f"My name is {full}.", en),
        (f"我是{without_title}。", zh),
        (f"我是{first}。", zh),
    ]
    for text, expected in positives:
        cleaned, changed = scrub_identity_text(text, principal=principal)
        assert changed, text
        assert cleaned == expected, text
