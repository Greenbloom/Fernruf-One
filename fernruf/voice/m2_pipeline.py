from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from pipecat_asterisk import AsteriskWebsocketTransport

from fernruf.call_settings.resolver import resolve_llm_endpoint_for_call
from fernruf.mood import MoodTurn
from fernruf.voice.processors.mood_turn import MoodTurnProcessor
from fernruf.llm.extra import LlmReasoningMode
from fernruf.llm.service_factory import create_fernruf_llm_service
from fernruf.settings import Settings
from fernruf.voice.call_session import M2CallSession, coerce_tool_str
from fernruf.voice.hooks import M2CallHooks
from fernruf.voice.latency import LatencyLog
from fernruf.voice.processors.coalesce_user_turns import (
    CoalesceUserTurnFlushProcessor,
    CoalesceUserTurnGateProcessor,
)
from fernruf.voice.processors.context_session_sync import ContextSessionSyncProcessor
from fernruf.voice.processors.call_hangup import CallHangup
from fernruf.voice.processors.identity_guard import IdentityOutputGuardProcessor
from fernruf.voice.processors.filler import LlmFillerProcessor
from fernruf.voice.processors.filler_state import LlmFillerState
from fernruf.voice.processors.llm_failure import LlmFailureProcessor
from fernruf.voice.processors.merge_user_context import MergeConsecutiveUserContextProcessor
from fernruf.voice.processors.consolidate_spoken_assistant import (
    ConsolidateSpokenAssistantProcessor,
)
from fernruf.voice.processors.dedupe_tool_calls import DedupeToolCallFramesProcessor
from fernruf.voice.processors.spoken_assistant_context import SpokenAssistantContextProcessor
from fernruf.voice.processors.tool_text_guard import ToolTextGuardProcessor
from fernruf.voice.processors.metrics import ConversationMetricsProcessor
from fernruf.voice.processors.opener import DisclosureOpenerProcessor
from fernruf.voice.processors.verify_flow import VerifyFlowProcessor, VerifyFlowState
from fernruf.language import phrase as call_phrase
from fernruf.stt.whisper import log_live_stt_kwargs, whisper_language_code
from fernruf.voice.call_phase import farewell_in_text
from fernruf.voice.must_say import brief_requirements, goal_needs_answer
from fernruf.voice.prompt import build_system_prompt
from fernruf.voice.services.faster_whisper_stt import FasterWhisperSTTService
from fernruf.voice.services.fish_rest_tts import FishRestTTSService
from fernruf.voice.services.mock_llm import MockLLMService

LOG = logging.getLogger("fernruf.voice.pipeline")


@dataclass
class BuiltM2Pipeline:
    pipeline: Pipeline
    metrics: ConversationMetricsProcessor
    hangup: CallHangup
    stt: FasterWhisperSTTService
    session: M2CallSession
    context_pair: LLMContextAggregatorPair
    llm_context: LLMContext
    verify_state: VerifyFlowState | None = None
    verify_processor: VerifyFlowProcessor | None = None


def _llm_tools(hooks: M2CallHooks, session: M2CallSession, settings: Settings) -> list[Any]:
    async def send_dtmf(params: FunctionCallParams, digits: str) -> None:
        """Send DTMF digits on the active phone call (0-9, *, #, w pause)."""
        await hooks.send_dtmf(digits)

    async def end_call(params: FunctionCallParams, reason: str, goodbye: str = "") -> None:
        """Accept or refuse end_call. A refusal does not run the LLM again."""
        tool_id = getattr(params, "tool_call_id", None) or ""
        reason = coerce_tool_str(reason)
        goodbye = coerce_tool_str(goodbye)
        if tool_id and tool_id in session.handled_tool_call_ids:
            await params.result_callback(
                {"ok": True, "message": "Call is already ending."},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return
        if params.context is not None:
            messages = list(getattr(params.context, "messages", []) or [])
            session.sync_from_llm_messages(messages)
        if session.phase.is_hung_up or session.phase.is_ending:
            if hooks.schedule_hangup and session.phase.is_ending:
                await hooks.schedule_hangup()
            await params.result_callback(
                {"ok": True, "message": "Call is already ending."},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return
        err = session.validate_end_call(reason, settings, goodbye=goodbye)
        if err:
            LOG.warning("%s", err)
            await params.result_callback(
                {"ok": False, "error": err},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return
        spoken = session.in_flight_assistant_completion()
        if farewell_in_text(spoken, session.phase.goodbye_phrase or goodbye):
            goodbye = ""
        session.phase.begin_ending(
            reason=reason or "end_call",
            goodbye=goodbye,
            spoken=spoken,
            await_playback=bool(goodbye) or bool(spoken),
        )
        if tool_id:
            session.handled_tool_call_ids.add(tool_id)
        if hooks.schedule_hangup:
            await hooks.schedule_hangup()
        await params.result_callback(
            {
                "ok": True,
                "message": (
                    "Goodbye will be spoken and the call will end when playback finishes."
                    if goodbye
                    else "Hangup scheduled when the bot finishes speaking."
                ),
            },
            properties=FunctionCallResultProperties(run_llm=False),
        )

    return [send_dtmf, end_call]


def build_m2_pipeline(
    transport: AsteriskWebsocketTransport,
    *,
    settings: Settings,
    task: str,
    call_id: str,
    hooks: M2CallHooks,
    session: M2CallSession,
    llm_model_override: str | None = None,
    llm_reasoning_override: LlmReasoningMode | None = None,
    latency_log: LatencyLog | None = None,
    verify: bool = False,
    verify_state: VerifyFlowState | None = None,
    goal: str = "",
    must_say: tuple[str, ...] | None = None,
    opening_request: str = "",
) -> BuiltM2Pipeline:
    parsed_goal, parsed_must = brief_requirements(task)
    session.phase.must_say = parsed_must if must_say is None else tuple(must_say)
    session.phase.goal_needs_answer = goal_needs_answer(goal or parsed_goal)
    session.phase.call_language = (settings.call_language or "de").strip() or "de"
    session.phase.goodbye_phrase = call_phrase(settings, "goodbye")
    LOG.info(
        "call start detected_language=%s whisper_language=%s",
        getattr(settings, "_detected_language_label", None) or settings.call_language,
        whisper_language_code(settings),
    )
    log_live_stt_kwargs(settings)
    ep, resolution = resolve_llm_endpoint_for_call(
        settings, call_model_override=llm_model_override
    )
    LOG.info(
        "M2 pipeline call=%s llm model=%s provider=%s source=%s verify=%s",
        call_id,
        ep.model,
        ep.provider,
        resolution.source,
        verify,
    )

    flow_state = verify_state if verify_state is not None else (
        VerifyFlowState() if verify else None
    )

    stt = FasterWhisperSTTService(
        call_settings=settings,
        mock_text=settings.verify_stt_fixture if verify else None,
        verify_state=flow_state,
    )
    tts_synth_log = flow_state.tts_synth_log if flow_state is not None else None
    mood = MoodTurn(settings, session=session)
    tts = FishRestTTSService(
        call_settings=settings,
        use_fixture=False,
        synth_log=tts_synth_log if verify else None,
        mood=mood,
    )

    system = build_system_prompt(settings, task)
    tools = [] if (verify or settings.llm_mock) else _llm_tools(hooks, session, settings)
    context = LLMContext(messages=[], tools=tools)
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    on_dtmf = None
    if flow_state is not None:

        async def _tap_dtmf(digits: str) -> None:
            flow_state.dtmf_sent += digits

        on_dtmf = _tap_dtmf

    if verify or settings.llm_mock:
        llm: Any = MockLLMService(hooks=hooks, task=task, on_dtmf=on_dtmf)
    else:
        llm = create_fernruf_llm_service(
            settings,
            model=ep.model,
            provider=ep.provider,
            system_instruction=system,
            reasoning_override=llm_reasoning_override,
            session=session,
        )

    metrics = ConversationMetricsProcessor(
        call_id=call_id,
        provider=ep.provider,
        model=ep.model,
        latency_log=latency_log,
        session=session,
    )
    from fernruf.disclosure import spoken_ai_disclosure

    disclosure_text = (settings.disclosure_prefix or "").strip() or spoken_ai_disclosure(
        settings
    )
    opener = DisclosureOpenerProcessor(
        disclosure=disclosure_text,
        privacy_line=settings.spoken_privacy_line,
        request=opening_request,
        session=session,
    )
    filler_state = LlmFillerState()
    filler_gate = LlmFillerProcessor(
        phrase=settings.filler_phrase,
        delay_s=settings.llm_filler_delay_s,
        state=filler_state,
        session=session,
    )
    llm_failure = LlmFailureProcessor(hooks=hooks, session=session, settings=settings)
    output = transport.output()
    hangup = CallHangup(
        hooks=hooks,
        session=session,
        settings=settings,
        playout=getattr(output, "playout_clock", None),
        request_queue_drained=getattr(output, "request_queue_drained", None),
        accept_queue_drained=getattr(output, "accept_queue_drained", None),
        seal_playout=getattr(output, "seal_playout", None),
        output_pending=getattr(output, "output_audio_pending", None),
        note_queued_audio=getattr(output, "note_audio_queued", None),
    )

    async def _schedule_hangup_from_tool() -> None:
        await hangup.arm()

    hooks.schedule_hangup = _schedule_hangup_from_tool
    context_sync = ContextSessionSyncProcessor(session=session)
    coalesce_gate = CoalesceUserTurnGateProcessor(session=session)
    coalesce_flush = CoalesceUserTurnFlushProcessor(session=session)
    merge_user_context = MergeConsecutiveUserContextProcessor()
    spoken_context = SpokenAssistantContextProcessor()
    consolidate_spoken = ConsolidateSpokenAssistantProcessor(session=session)
    dedupe_tool_calls = DedupeToolCallFramesProcessor()
    identity_guard = IdentityOutputGuardProcessor(
        principal=settings.require_principal(),
        replacement=call_phrase(settings, "identity_line"),
    )
    tool_text_guard = ToolTextGuardProcessor()
    mood_on_transcript = MoodTurnProcessor(mood, session=session)
    mood_on_turn = MoodTurnProcessor(mood, session=session)

    processors: list[Any] = [
        transport.input(),
        opener,
        stt,
        mood_on_transcript,
        coalesce_gate,
        pair.user(),
        context_sync,
        mood_on_turn,
        merge_user_context,
        llm_failure,
        llm,
        coalesce_flush,
        filler_gate,
        spoken_context,
        identity_guard,
    ]

    verify_processor: VerifyFlowProcessor | None = None
    if verify and flow_state is not None:
        verify_processor = VerifyFlowProcessor(
            settings=settings,
            hooks=hooks,
            latency_log=latency_log or LatencyLog(settings.latency_log_path),
            metrics=metrics,
            state=flow_state,
        )
        processors.append(verify_processor)
        verify_state = flow_state

    processors.extend(
        [
            tool_text_guard,
            tts,
            consolidate_spoken,
            dedupe_tool_calls,
            pair.assistant(),
            hangup,
            metrics,
            transport.output(),
        ]
    )

    pipeline = Pipeline(processors)
    return BuiltM2Pipeline(
        pipeline=pipeline,
        metrics=metrics,
        hangup=hangup,
        stt=stt,
        session=session,
        verify_state=verify_state,
        verify_processor=verify_processor,
        context_pair=pair,
        llm_context=context,
    )
