"""STT finals are the callee transcript, not whatever the aggregator committed."""

from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from fernruf.voice.call_session import M2CallSession
from fernruf.voice.processors.mood_turn import MoodTurnProcessor


class _Turn:
    def start(self, text: str, prior_bot: str | None = None) -> None:
        del text, prior_bot


def test_every_stt_final_is_stored_in_order() -> None:
    session = M2CallSession()
    session.mark_answered(time.monotonic() - 1.0)
    session.note_transcript_line("agent", "Hello, this is an automated call.")
    processor = MoodTurnProcessor(_Turn(), session=session)  # type: ignore[arg-type]

    async def capture(frame, direction=FrameDirection.DOWNSTREAM) -> None:  # noqa: ANN001
        del frame, direction

    processor.push_frame = capture  # type: ignore[method-assign]
    async def say(text: str) -> None:
        await processor.process_frame(
            TranscriptionFrame(text=text, user_id="u", timestamp="t"),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.01)

    async def run() -> None:
        await say("I have like two bottles.")
        # The aggregator committed a different segment. It must not replace
        # the final or block lines that arrive after the goodbye.
        session.observe_committed_lines(
            [
                ("agent", "Hello, this is an automated call."),
                ("callee", "That's the next one."),
                ("agent", "How many do you have?"),
            ]
        )
        session.note_transcript_line("agent", "Thank you. Goodbye.")
        await say("Goodbye.")
        await say("Hello?")
        await say("You didn't hang up, bro.")

    asyncio.run(run())
    callee = [line["text"] for line in session.transcript_lines if line["speaker"] == "callee"]
    assert callee == [
        "I have like two bottles.",
        "Goodbye.",
        "Hello?",
        "You didn't hang up, bro.",
    ]
    assert "That's the next one." not in callee
    stamps = [int(line["t_ms"]) for line in session.transcript_lines]
    assert stamps == sorted(stamps)
    assert stamps[0] >= 1000
    assert session.transcript_lines[-1]["speaker"] == "callee"
    assert session.transcript_lines[-1]["text"] == "You didn't hang up, bro."
