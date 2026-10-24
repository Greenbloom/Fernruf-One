from __future__ import annotations

import pytest
from click.testing import CliRunner

from fernruf.cli import main


def test_stt_smoke_without_principal_does_not_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FERNRUF_ENV_FILE", "none")
    monkeypatch.delenv("FERNRUF_PRINCIPAL_NAME", raising=False)
    monkeypatch.setenv("FERNRUF_VOICE_MOCK", "false")
    runner = CliRunner()
    result = runner.invoke(main, ["stt-smoke"], catch_exceptions=False)
    assert result.exit_code in (0, 1, 2)
    assert "Traceback" not in result.output
