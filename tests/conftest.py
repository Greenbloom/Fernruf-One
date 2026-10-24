"""Shared pytest defaults for Fernruf."""

from __future__ import annotations

import pytest

# Two distinct names so disclosure/prompt tests cannot pass with a hardcoded principal.
PRINCIPAL_TEST_NAMES = ("Erika Muster", "Jordan Lee")
DEFAULT_TEST_PRINCIPAL = PRINCIPAL_TEST_NAMES[0]


@pytest.fixture(autouse=True)
def _fernruf_test_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNRUF_PRINCIPAL_NAME", DEFAULT_TEST_PRINCIPAL)
    monkeypatch.setenv("FERNRUF_QUIET_HOURS_ALLOW_OFF", "true")
    monkeypatch.setenv("FERNRUF_LLM_MODEL", "openrouter/test-model")
