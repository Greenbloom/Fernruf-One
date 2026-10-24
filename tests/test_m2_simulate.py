import asyncio
import json
from pathlib import Path

from fernruf.settings import Settings
from fernruf.voice.simulate import SimulateConversation


def test_m2_simulate_opener_and_transcript(tmp_path: Path):
    s = Settings(
        _env_file=None,
        principal_name="Alex Example",
        llm_model="openrouter/test-model",
        FERNRUF_LLM_MOCK=True,
        FERNRUF_VOICE_MOCK=True,
        FERNRUF_LATENCY_LOG=str(tmp_path / "latency.jsonl"),
        FERNRUF_SETTINGS_DB=str(tmp_path / "fernruf.sqlite"),
    )
    result = asyncio.run(SimulateConversation(s, "Termin abstimmen").run())
    assert result.transcript[0].speaker == "agent"
    assert "KI-Assistent" in result.transcript[0].text
    assert any(l.speaker == "callee" for l in result.transcript)
    assert result.latency_path and result.latency_path.exists()
    lines = result.latency_path.read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[0])
    assert row["llm_provider"] in ("openrouter", "openai_compatible")
    assert row["llm_model"]


def test_m2_simulate_speaks_privacy_line_when_enabled(tmp_path: Path) -> None:
    from fernruf.disclosure import spoken_privacy_line

    s = Settings(
        _env_file=None,
        principal_name="Alex Example",
        llm_model="openrouter/test-model",
        FERNRUF_LLM_MOCK=True,
        FERNRUF_VOICE_MOCK=True,
        FERNRUF_SPOKEN_PRIVACY_ENABLED=True,
        FERNRUF_LATENCY_LOG=str(tmp_path / "latency.jsonl"),
        FERNRUF_SETTINGS_DB=str(tmp_path / "fernruf.sqlite"),
    )
    expected = spoken_privacy_line(s, language="de")
    result = asyncio.run(SimulateConversation(s, "Termin abstimmen").run())
    agent_lines = [line.text for line in result.transcript if line.speaker == "agent"]
    assert agent_lines[0]
    assert expected in agent_lines[1]
