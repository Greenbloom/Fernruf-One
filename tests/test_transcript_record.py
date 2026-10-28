"""Recording bugs from the first live MCP call: one agent line, farewell, real t_ms."""

from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TextType,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from fernruf.ari.m2 import _transcript_from_session
from fernruf.quiet_hours import BERLIN
from fernruf.service.line_health import StaticLineHealth
from fernruf.service.manager import CallManager
from fernruf.service.runner import CallJob, CallRunResult
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.m2_pipeline import _llm_tools
from fernruf.voice.processors.call_hangup import CallHangup
from fernruf.voice.processors.consolidate_spoken_assistant import (
    ConsolidateSpokenAssistantProcessor,
)
from fernruf.voice.processors.spoken_assistant_context import SpokenAssistantContextProcessor
from fernruf.voice.services.fish_rest_tts import FishRestTTSService
from tests.m3_support import service_settings

FAREWELL = "Vielen Dank. Dann meldet sich Alex am Montag um 10:30 Uhr. Auf Wiederhören."


class _StopAfterTts(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStoppedFrame):
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)


def _speak_one_turn(text: str) -> tuple[list[str], list[str], list[dict]]:
    async def run() -> tuple[list[str], list[str], list[dict]]:
        settings = Settings(_env_file=None, FERNRUF_VOICE_MOCK="true")
        session = M2CallSession()
        session.mark_answered(time.monotonic())
        context = LLMContext(messages=[])
        pair = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams())
        synth: list[str] = []
        pipeline = Pipeline(
            [
                pair.user(),
                SpokenAssistantContextProcessor(),
                FishRestTTSService(call_settings=settings, use_fixture=True, synth_log=synth),
                ConsolidateSpokenAssistantProcessor(session=session),
                pair.assistant(),
                _StopAfterTts(),
            ]
        )
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000),
        )
        await runner.add_workers(worker)
        run_task = asyncio.create_task(runner.run())
        await worker.queue_frame(StartFrame())
        await asyncio.sleep(0.2)
        await worker.queue_frame(LLMFullResponseStartFrame())
        await worker.queue_frame(LLMTextFrame(text=text))
        await worker.queue_frame(LLMFullResponseEndFrame())
        await asyncio.sleep(1.5)
        assistants = [
            m.get("content", "")
            for m in context.messages
            if isinstance(m, dict) and m.get("role") == "assistant"
        ]
        lines = list(session.transcript_lines)
        spoken = list(synth)
        await worker.cancel()
        try:
            await asyncio.wait_for(run_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        return spoken, assistants, lines

    return asyncio.run(run())


def test_spoken_agent_utterance_is_stored_once() -> None:
    """Fish used to re-push the consolidated append, so each agent line was stored twice."""
    spoken, assistants, lines = _speak_one_turn("Vielen Dank. Auf Wiederhören.")
    assert spoken == ["Vielen Dank.", "Auf Wiederhören."]
    assert assistants == ["Vielen Dank. Auf Wiederhören."]
    assert [ln["text"] for ln in lines] == ["Vielen Dank. Auf Wiederhören."]
    assert lines[0]["speaker"] == "agent"
    assert lines[0]["t_ms"] >= 0


RELEASE = (
    "The ExampleApp test release v1.0.0 is ready. "
    "The download link is in your chat with Example Bot."
)


def test_must_say_release_line_is_recorded_once() -> None:
    """Synthetic call showed the combined must_say line at t_ms 2 and 3.

    must_say is prompt text, not a second speaker. One LLM turn was stored twice
    because Fish re-pushed the upstream consolidated append. TTS still runs once
    per sentence.
    """
    spoken, assistants, lines = _speak_one_turn(RELEASE)
    assert spoken == [
        "The ExampleApp test release v1.0.0 is ready.",
        "The download link is in your chat with Example Bot.",
    ]
    assert assistants == [RELEASE]
    assert [ln["text"] for ln in lines if ln["speaker"] == "agent"] == [RELEASE]


def test_end_call_farewell_is_flushed_before_finalize() -> None:
    session = M2CallSession()
    session.mark_answered(time.monotonic() - 1.0)
    session.note_transcript_line("callee", "Montag um 10:30 Uhr passt.")
    session.append_llm_completion(FAREWELL)
    lines = _transcript_from_session(session)
    assert lines[-1]["speaker"] == "agent"
    assert lines[-1]["text"] == FAREWELL
    assert lines[-2]["speaker"] == "callee"
    assert _transcript_from_session(session) == lines


GOODBYE = "Vielen Dank, dann meldet sich der Auftraggeber. Auf Wiederhören."


def test_end_call_goodbye_is_the_last_transcript_line() -> None:
    """end_call speaks the goodbye, then hangs up without BotStoppedSpeaking.

    The consolidator only commits TTSTextFrame when append_to_context is set,
    and only on BotStoppedSpeaking. The goodbye is a TTSSpeakFrame
    (append_to_context false) and hangup follows QUEUE_DRAINED, so that frame
    never commits the line. The spoken goodbye still has to be the last entry.
    """
    from unittest.mock import AsyncMock, MagicMock

    from pipecat.processors.frame_processor import FrameDirection

    session = M2CallSession()
    session.mark_answered(time.monotonic() - 1.0)
    session.note_user_transcript("Montag um zehn Uhr passt.")
    session.note_transcript_line("callee", "Montag um zehn Uhr passt.")
    settings = Settings(
        _env_file=None,
        min_user_turns_before_hangup=1,
        min_call_duration_before_hangup_s=0.0,
        hangup_after_bot_stopped_margin_s=0.05,
    )
    hangup = AsyncMock()
    hooks = M2CallHooks(send_dtmf=AsyncMock(), hangup=hangup, session=session)
    end_call = _llm_tools(hooks, session, settings)[1]
    params = MagicMock()
    params.tool_call_id = "call-end"
    params.context = None
    params.result_callback = AsyncMock()

    async def run() -> None:
        await end_call(params, reason="Termin steht", goodbye=GOODBYE)
        guard = CallHangup(hooks=hooks, session=session, settings=settings)
        consolidator = ConsolidateSpokenAssistantProcessor(session=session)
        pushed: list = []

        async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
            pushed.append(frame)

        guard.push_frame = capture  # type: ignore[method-assign]
        consolidator.push_frame = capture  # type: ignore[method-assign]

        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        speaks = [frame for frame in pushed if isinstance(frame, TTSSpeakFrame)]
        assert len(speaks) == 1
        assert speaks[0].append_to_context is False

        spoken = TTSTextFrame(GOODBYE, TextType.SENTENCE)
        spoken.append_to_context = False
        await consolidator.process_frame(spoken, FrameDirection.DOWNSTREAM)
        await consolidator.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        assert session.transcript_lines[-1]["speaker"] == "callee"

        await guard.process_frame(
            TTSStartedFrame(context_id="bye"), FrameDirection.DOWNSTREAM
        )
        await guard.process_frame(spoken, FrameDirection.DOWNSTREAM)
        await guard.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        await guard.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert session.hung_up
    lines = _transcript_from_session(session)
    assert lines[-1]["speaker"] == "agent"
    assert lines[-1]["text"] == GOODBYE
    assert lines[-2]["speaker"] == "callee"
    assert _transcript_from_session(session) == lines


def test_pipeline_end_records_final_agent_turn_without_bot_stopped() -> None:
    """QUEUE_DRAINED hangup ends the pipeline before BotStoppedSpeaking."""
    from pipecat.processors.frame_processor import FrameDirection

    farewell = "Vielen Dank. Auf Wiederhören."
    session = M2CallSession()
    session.mark_answered(time.monotonic() - 1.0)
    session.note_transcript_line("callee", "Ja, das passt.")
    proc = ConsolidateSpokenAssistantProcessor(session=session)

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):  # noqa: ANN001
        return None

    proc.push_frame = capture  # type: ignore[method-assign]

    async def run() -> None:
        await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(
            TTSTextFrame(farewell, TextType.SENTENCE),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())
    lines = _transcript_from_session(session)
    assert lines[-1]["speaker"] == "agent"
    assert lines[-1]["text"] == farewell
    assert lines[-2]["speaker"] == "callee"


def test_t_ms_is_milliseconds_since_answer_and_monotonic() -> None:
    session = M2CallSession()
    session.mark_answered(time.monotonic() - 2.5)
    session.note_transcript_line("agent", "Hallo.")
    time.sleep(0.05)
    session.note_transcript_line("callee", "Ja.")
    session.note_transcript_line("callee", "Ja.")
    first, second = session.transcript_lines
    assert first["t_ms"] >= 2500
    assert second["t_ms"] >= first["t_ms"]
    assert second["t_ms"] - first["t_ms"] < 2000
    assert [ln["t_ms"] for ln in session.transcript_lines] != [0, 1]
    assert len(session.transcript_lines) == 2


def test_fallback_transcript_drops_duplicate_agent_lines() -> None:
    session = M2CallSession()
    session.mark_answered(time.monotonic() - 3.0)
    session.context_messages = [
        {"role": "assistant", "content": "Hallo."},
        {"role": "assistant", "content": "Hallo."},
        {"role": "user", "content": "Ja."},
    ]
    lines = _transcript_from_session(session)
    assert [ln["text"] for ln in lines] == ["Hallo.", "Ja."]
    assert lines[0]["t_ms"] >= 3000
    assert lines[1]["t_ms"] >= lines[0]["t_ms"]


def test_answered_at_is_before_ended_at(tmp_path) -> None:
    from datetime import datetime

    noon = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
    settings, _token = service_settings(tmp_path)

    async def stamped(job: CallJob) -> CallRunResult:
        return CallRunResult(
            outcome="completed",
            answered=True,
            answered_at="2026-06-15T11:59:04+02:00",
            duration_s=56.0,
            transcript=[{"t_ms": 1200, "speaker": "agent", "text": "Hallo."}],
        )

    manager = CallManager(
        settings,
        line_health=StaticLineHealth(True),
        runner=stamped,
        now=lambda: noon,
    )

    async def run():
        queued = await manager.place(to="+493023125123", task={"goal": "Test"})
        for _ in range(50):
            view = manager.get_call(queued["call_id"])
            if view["status"] == "finalized":
                return view
            await asyncio.sleep(0.02)
        raise AssertionError("call did not finish")

    view = asyncio.run(run())
    assert view["answered_at"] == "2026-06-15T11:59:04+02:00"
    assert view["ended_at"] == noon.isoformat()
    assert view["answered_at"] < view["ended_at"]


def test_simulated_answer_is_not_copied_from_hangup(tmp_path) -> None:
    from datetime import datetime, timedelta

    noon = datetime(2026, 6, 15, 12, 0, tzinfo=BERLIN)
    settings, _token = service_settings(tmp_path)

    async def unstamped(job: CallJob) -> CallRunResult:
        return CallRunResult(outcome="completed", answered=True, duration_s=56.0, transcript=[])

    manager = CallManager(
        settings,
        line_health=StaticLineHealth(True),
        runner=unstamped,
        now=lambda: noon,
    )

    async def run():
        queued = await manager.place(to="+493023125123", task={"goal": "Test"})
        for _ in range(50):
            view = manager.get_call(queued["call_id"])
            if view["status"] == "finalized":
                return view
            await asyncio.sleep(0.02)
        raise AssertionError("call did not finish")

    view = asyncio.run(run())
    assert view["answered_at"] == (noon - timedelta(seconds=56)).isoformat()
    assert view["answered_at"] < view["ended_at"]
