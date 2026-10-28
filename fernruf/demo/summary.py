"""Short outcome file written next to the demo recording."""

from __future__ import annotations


def build_demo_summary(
    *,
    call_id: str,
    task: str,
    persona: str,
    transcript: list[dict],
    duration_s: float,
    answered: bool,
) -> dict:
    agent_lines = [
        str(turn.get("text") or "").strip()
        for turn in transcript
        if turn.get("speaker") == "agent" and str(turn.get("text") or "").strip()
    ]
    last_is_agent = bool(transcript) and transcript[-1].get("speaker") == "agent"
    status = "completed" if answered and last_is_agent and agent_lines else "incomplete"
    return {
        "call_id": call_id,
        "task": task,
        "persona": persona,
        "task_status": status,
        "one_line": agent_lines[-1] if agent_lines else "",
        "duration_s": round(float(duration_s), 3),
        "turns": len(transcript),
        "local_only": True,
    }
