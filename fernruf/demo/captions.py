"""SRT captions aligned to the MixMonitor recording.

Transcript ``t_ms`` is milliseconds from answer. The dialplan starts
MixMonitor on the same ``Answer()`` step, so those times match the wav.
"""

from __future__ import annotations

FERNRUF_CAPTION = "Fernruf (AI)"
PRAXIS_CAPTION = "Praxis (AI)"


def speaker_caption(speaker: str) -> str:
    if speaker == "agent":
        return FERNRUF_CAPTION
    return PRAXIS_CAPTION


def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, milli = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milli:03d}"


def transcript_to_srt(
    turns: list[dict],
    *,
    duration_ms: int | None = None,
) -> str:
    usable = [turn for turn in turns if str(turn.get("text") or "").strip()]
    cues: list[str] = []
    for index, turn in enumerate(usable):
        start = int(turn.get("t_ms") or 0)
        text = " ".join(str(turn.get("text") or "").split())
        if index + 1 < len(usable):
            # Stay up until the next line. A short copied wav must not
            # collapse that gap to a flash.
            end = int(usable[index + 1].get("t_ms") or 0) - 80
        elif duration_ms is not None and int(duration_ms) > start:
            end = int(duration_ms)
        else:
            end = start + max(1200, len(text) * 80)
        if end < start + 400:
            end = start + 400
        label = speaker_caption(str(turn.get("speaker") or ""))
        cues.append(f"{index + 1}\n{_fmt(start)} --> {_fmt(end)}\n{label}: {text}\n")
    if not cues:
        return ""
    return "\n".join(cues) + "\n"
