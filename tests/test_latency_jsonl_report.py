from __future__ import annotations

import json
from pathlib import Path

from fernruf.voice.latency import LatencyLog


def test_median_report_from_jsonl_filters_call_id(tmp_path: Path) -> None:
    path = tmp_path / "latency.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "call_id": "aaa",
                        "turn": 1,
                        "llm_provider": "openrouter",
                        "llm_model": "example/model-b",
                        "llm_first_token_ms": 900.0,
                        "fish_ttfa_ms": 500.0,
                    }
                ),
                json.dumps(
                    {
                        "call_id": "bbb",
                        "turn": 1,
                        "llm_provider": "openrouter",
                        "llm_model": "example/model-b",
                        "llm_first_token_ms": 2000.0,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    report = LatencyLog.median_report_from_jsonl(path, call_id="aaa")
    assert report["turns"] == 1.0
    assert report["llm_first_token_ms_median"] == 900.0
    assert report["fish_ttfa_ms_median"] == 500.0
