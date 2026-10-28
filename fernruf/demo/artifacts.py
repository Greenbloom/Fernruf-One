"""Write the demo transcript, captions, and summary."""

from __future__ import annotations

import json
from pathlib import Path

from fernruf.data_dir import prepare_parent
from fernruf.demo.captions import transcript_to_srt
from fernruf.demo.plan import DemoCallPlan
from fernruf.demo.summary import build_demo_summary


def write_demo_texts(
    plan: DemoCallPlan,
    *,
    task: str,
    persona: str,
    transcript: list[dict],
    duration_s: float,
    answered: bool,
    duration_ms: int | None,
) -> dict[str, Path]:
    prepare_parent(plan.out / "transcript.json")
    plan.out.mkdir(parents=True, exist_ok=True)
    transcript_path = plan.out / "transcript.json"
    captions_path = plan.out / "captions.srt"
    summary_path = plan.out / "summary.json"
    summary = build_demo_summary(
        call_id=plan.call_id,
        task=task,
        persona=persona,
        transcript=transcript,
        duration_s=duration_s,
        answered=answered,
    )
    transcript_path.write_text(
        json.dumps(
            {"call_id": plan.call_id, "turns": transcript},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    captions_path.write_text(
        transcript_to_srt(transcript, duration_ms=duration_ms),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "transcript": transcript_path,
        "captions": captions_path,
        "summary": summary_path,
    }
