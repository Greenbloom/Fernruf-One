from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import Sequence
from typing import Any

from openai.types.chat import ChatCompletionChunk
from pipecat.frames.frames import LLMTextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.llm import OpenAILLMService

from fernruf.language import phrase
from fernruf.llm.stream_liveness import delta_has_llm_liveness
from fernruf.settings import Settings
from fernruf.voice.call_phase import assistant_turn_asks, farewell_in_text
from fernruf.voice.call_session import M2CallSession
from fernruf.voice.tool_text import ToolTextGate
from fernruf.voice.frames.metrics_frames import LlmFirstTokenFrame, LlmInferenceStartedFrame
from fernruf.voice.processors.merge_user_context import merge_consecutive_user_messages

LOG = logging.getLogger("fernruf.voice.llm")


def _delta_field(delta: Any, name: str) -> str | None:
    if delta is None:
        return None
    val = getattr(delta, name, None)
    if val is None and hasattr(delta, "model_extra") and delta.model_extra:
        val = delta.model_extra.get(name)
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return str(val)


def _chunk_provider(chunk: ChatCompletionChunk) -> str | None:
    prov = getattr(chunk, "provider", None)
    if prov:
        return str(prov)
    extra = getattr(chunk, "model_extra", None) or {}
    if isinstance(extra, dict) and extra.get("provider"):
        return str(extra["provider"])
    return None


class FernrufOpenAILLMService(OpenAILLMService):
    """OpenAI-compatible LLM with voice-call logging and OpenRouter reasoning visibility."""

    def __init__(
        self,
        *,
        call_settings: Settings | None = None,
        call_session: M2CallSession | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._call_settings = call_settings
        self._call_session = call_session
        self._completion_skipped = False
        self._primary_model: str = ""
        if hasattr(self, "_settings") and self._settings is not None:
            self._primary_model = getattr(self._settings, "model", "") or ""

    def _logged_model(self) -> str:
        name = ""
        try:
            name = self.get_full_model_name() or ""
        except Exception:
            name = ""
        if not name and hasattr(self, "_settings") and self._settings is not None:
            name = getattr(self._settings, "model", "") or ""
        if not name:
            name = self._primary_model
        return name or "unknown"

    def _fallback_model(self) -> str | None:
        if not self._call_settings:
            return None
        fb = (self._call_settings.llm_fallback_model or "").strip()
        if not fb:
            return None
        return fb

    def _apply_model(self, model: str) -> None:
        self.set_full_model_name(model)
        if hasattr(self, "_settings") and self._settings is not None:
            self._settings.model = model

    async def run_function_calls(self, function_calls: Sequence[FunctionCallFromLLM]) -> None:
        if self._call_session is not None and function_calls:
            unique: list[FunctionCallFromLLM] = []
            for fc in function_calls:
                tid = fc.tool_call_id or ""
                if tid and tid in self._call_session.handled_tool_call_ids:
                    LOG.warning("Skipping duplicate tool execution for %s", tid)
                    continue
                unique.append(fc)
            function_calls = unique
        if function_calls:
            await super().run_function_calls(function_calls)

    async def _push_llm_text(self, text: str) -> None:
        if self._call_session is not None and self._call_session.phase.suppresses_llm:
            return
        if self._call_session is not None:
            self._call_session.speakable_llm_this_turn = True
        await self.push_frame(LLMTextFrame(text))

    async def get_chat_completions(self, context: LLMContext):  # noqa: ANN201
        if self._call_session is not None and self._call_session.phase.suppresses_llm:
            self._completion_skipped = True
            LOG.info("Skipping LLM HTTP; call is ending")

            async def _empty():  # noqa: ANN202
                if False:
                    yield  # pragma: no cover

            return _empty()
        self._completion_skipped = False
        return await super().get_chat_completions(context)

    async def _process_context(self, context: LLMContext) -> None:
        messages = list(getattr(context, "messages", []) or [])
        merged = merge_consecutive_user_messages(messages)
        if merged != messages:
            context.set_messages(merged)
        if self._call_session is not None:
            self._call_session.sync_from_llm_messages(merged)
        await self.push_frame(LlmInferenceStartedFrame(), FrameDirection.DOWNSTREAM)
        if self._call_session is not None and self._call_session.phase.suppresses_llm:
            LOG.info("Skipping LLM inference; call is ending")
            return
        if self._call_session is not None:
            self._call_session.llm_in_flight = True
            self._call_session.llm_turns += 1
        try:
            await self._process_context_with_timeout(context)
        finally:
            if self._call_session is not None:
                self._call_session.llm_in_flight = False

    async def _speak_pending_reask(self) -> None:
        session = self._call_session
        if session is None or not session.phase.pending_reask or session.phase.reask_spoken:
            return
        if session.phase.is_ending or session.phase.is_hung_up:
            session.phase.pending_reask = False
            return
        session.phase.reask_spoken = True
        session.phase.reask_used = True
        session.phase.pending_reask = False
        session.phase.question_open = True
        session.phase.callee_spoke_since_question = False
        session.phase.awaiting_answer = True
        text = "Sorry, I didn't catch that. Could you say that once more?"
        if self._call_settings is not None:
            text = phrase(self._call_settings, "reask")
        LOG.info("Reasking once; the answer was unclear or missing")
        session.speakable_llm_this_turn = True
        await self.push_frame(
            TTSSpeakFrame(text=text, append_to_context=False),
            FrameDirection.DOWNSTREAM,
        )

    async def _push_llm_delay_apology(self) -> None:
        if self._call_session is not None and not self._call_session.phase.note_apology():
            LOG.info("Skipping LLM delay apology; ending or already played")
            return
        phrase = (
            self._call_settings.llm_delay_apology_phrase
            if self._call_settings
            else "Entschuldigung, ich habe gerade eine kleine Verzögerung."
        )
        if self._call_session is not None:
            self._call_session.speakable_llm_this_turn = True
        LOG.warning("Playing LLM delay apology model=%s", self._logged_model())
        await self.push_frame(
            TTSSpeakFrame(text=phrase, append_to_context=False),
            FrameDirection.DOWNSTREAM,
        )

    async def _process_context_with_timeout(self, context: LLMContext) -> None:
        timeout_s = (
            self._call_settings.llm_first_token_timeout_s if self._call_settings else 0.0
        )
        primary = self._logged_model()
        if not self._primary_model:
            self._primary_model = primary
        fallback = self._fallback_model()
        tried_fallback = False
        last_err: BaseException | None = None

        while True:
            try:
                await self._stream_context(context, first_token_timeout_s=timeout_s)
                return
            except asyncio.TimeoutError as exc:
                last_err = exc
                if not tried_fallback and fallback and self._logged_model() != fallback:
                    tried_fallback = True
                    LOG.warning(
                        "LLM first-token timeout after %.1fs; switching to fallback model=%s "
                        "(was %s)",
                        timeout_s,
                        fallback,
                        self._logged_model(),
                    )
                    self._apply_model(fallback)
                    if self._call_session is not None:
                        self._call_session.llm_used_fallback = True
                    continue
                if self._call_session is not None and self._call_session.phase.suppresses_llm:
                    LOG.info(
                        "LLM timeout suppressed during call ending model=%s",
                        self._logged_model(),
                    )
                    return
                LOG.error(
                    "LLM first-token timeout after %.1fs; no fallback left model=%s",
                    timeout_s,
                    self._logged_model(),
                )
                await self._push_llm_delay_apology()
                raise
        if last_err:
            raise last_err

    async def _mark_first_liveness(
        self,
        *,
        request_started: float,
        kind: str,
    ) -> None:
        await self.push_frame(LlmFirstTokenFrame(), FrameDirection.DOWNSTREAM)
        user_ms = (
            (time.monotonic() - self._call_session.last_user_turn_stop_mono) * 1000
            if self._call_session and self._call_session.last_user_turn_stop_mono
            else None
        )
        http_ms = (time.monotonic() - request_started) * 1000
        LOG.info(
            "LLM first token (%s) http=%.0fms user_turn=%s model=%s",
            kind,
            http_ms,
            f"{user_ms:.0f}ms" if user_ms is not None else "—",
            self._logged_model(),
        )

    async def _stream_context(
        self,
        context: LLMContext,
        *,
        first_token_timeout_s: float,
    ) -> None:
        functions_list: list[str] = []
        arguments_list: list[str] = []
        tool_id_list: list[str] = []
        func_idx = 0
        function_name = ""
        arguments = ""
        tool_call_id = ""

        await self.start_ttfb_metrics()
        chunk_stream = await self.get_chat_completions(context)

        @asynccontextmanager
        async def closing(stream):  # noqa: ANN001
            chunk_iter = stream.__aiter__()
            try:
                yield chunk_iter
            finally:
                if hasattr(chunk_iter, "aclose"):
                    await chunk_iter.aclose()
                if hasattr(stream, "close"):
                    await stream.close()
                elif hasattr(stream, "aclose"):
                    await stream.aclose()

        token_usage: LLMTokenUsage | None = None
        spoken_len = 0
        reasoning_len = 0
        logged_first_content = False
        logged_provider = False
        finish_reason: str | None = None
        first_liveness_seen = False
        request_started = time.monotonic()
        first_byte_at: float | None = None
        gate = ToolTextGate()
        held: list[str] = []
        phase = self._call_session.phase if self._call_session is not None else None
        hold_reask = phase is not None and phase.should_reask()
        # A farewell while the reask is still unanswered must not be spoken.
        # A second completion of the same turn would otherwise say goodbye anyway.
        hold_unanswered = (
            phase is not None
            and phase.awaiting_answer
            and not phase.callee_spoke_since_question
            and not hold_reask
        )
        hold = hold_reask or hold_unanswered
        if self._call_session is not None:
            self._call_session.reset_llm_completion()

        async def _accept_speakable(speakable: str) -> None:
            nonlocal spoken_len, logged_first_content
            if not speakable:
                return
            if self._call_session is not None:
                self._call_session.append_llm_completion(speakable)
            logged_first_content = True
            spoken_len += len(speakable)
            if hold:
                held.append(speakable)
                return
            await self._push_llm_text(speakable)

        async def _emit_model_text(text: str) -> None:
            await _accept_speakable(gate.feed(text))

        try:
            async with closing(chunk_stream) as chunk_iter:
                aiter = chunk_iter.__aiter__()

                async def _next_chunk() -> ChatCompletionChunk:
                    if first_byte_at is None:
                        return await aiter.__anext__()
                    if not first_liveness_seen and first_token_timeout_s > 0:
                        return await asyncio.wait_for(
                            aiter.__anext__(),
                            timeout=first_token_timeout_s,
                        )
                    return await aiter.__anext__()

                while True:
                    try:
                        chunk = await _next_chunk()
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        LOG.warning(
                            "LLM first-token timeout (no content, tool, or reasoning delta) "
                            "model=%s",
                            self._logged_model(),
                        )
                        raise

                    if first_byte_at is None:
                        first_byte_at = time.monotonic()

                    if chunk.usage:
                        token_usage = self._token_usage(chunk.usage)

                    if chunk.model and self._logged_model() != chunk.model:
                        self.set_full_model_name(chunk.model)

                    if not logged_provider:
                        provider = _chunk_provider(chunk)
                        gen_id = getattr(chunk, "id", None)
                        if provider or gen_id:
                            LOG.info(
                                "OpenRouter upstream provider=%s generation_id=%s model=%s",
                                provider or "?",
                                gen_id or "?",
                                self._logged_model(),
                            )
                            logged_provider = True

                    if chunk.choices is None or len(chunk.choices) == 0:
                        continue

                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    await self.stop_ttfb_metrics()

                    if not choice.delta:
                        continue

                    delta = choice.delta

                    if not first_liveness_seen and delta_has_llm_liveness(delta):
                        first_liveness_seen = True
                        if delta.tool_calls:
                            kind = "tool call"
                        elif _delta_field(delta, "content"):
                            kind = "content"
                        else:
                            kind = "reasoning"
                        await self._mark_first_liveness(
                            request_started=request_started,
                            kind=kind,
                        )

                    if delta.tool_calls:
                        await self.stop_ttfat_metrics()
                        if self._call_session is not None:
                            self._call_session.speakable_llm_this_turn = True
                        tool_call = delta.tool_calls[0]
                        if tool_call.index != func_idx:
                            functions_list.append(function_name)
                            arguments_list.append(arguments or "{}")
                            tool_id_list.append(tool_call_id)
                            function_name = ""
                            arguments = ""
                            tool_call_id = ""
                            func_idx += 1
                        if tool_call.function and tool_call.function.name:
                            function_name += tool_call.function.name
                            tool_call_id = tool_call.id
                        if tool_call.function and tool_call.function.arguments:
                            arguments += tool_call.function.arguments
                        continue

                    content = _delta_field(delta, "content") or ""
                    reasoning = (
                        _delta_field(delta, "reasoning")
                        or _delta_field(delta, "reasoning_content")
                        or ""
                    )
                    if reasoning:
                        reasoning_len += len(reasoning)

                    if content:
                        await _emit_model_text(content)
                    elif (
                        hasattr(delta, "audio")
                        and delta.audio
                        and delta.audio.get("transcript")
                    ):
                        await _emit_model_text(delta.audio["transcript"])
        finally:
            if token_usage:
                await self.start_llm_usage_metrics(token_usage)

        tail = gate.finish()
        if tail:
            await _accept_speakable(tail)
        if hold and self._call_session is not None:
            combined = "".join(held)
            names = [*functions_list, function_name]
            ending = any(name == "end_call" for name in names) or bool(gate.calls)
            phrase_for_match = self._call_session.phase.goodbye_phrase
            is_farewell = farewell_in_text(combined, phrase_for_match)
            if hold_reask and (ending or is_farewell):
                self._call_session.phase.pending_reask = True
            elif hold_unanswered and (ending or is_farewell):
                pass
            else:
                for part in held:
                    await self._push_llm_text(part)
                if hold_reask and assistant_turn_asks(combined):
                    self._call_session.phase.reask_used = True
        if (
            not any(name == "end_call" for name in (*functions_list, function_name))
            and gate.calls
        ):
            call = gate.calls[0]
            LOG.info(
                "Parsed textual end_call; speaking goodbye only (%s chars)",
                len(call.goodbye),
            )
            if function_name:
                functions_list.append(function_name)
                arguments_list.append(arguments or "{}")
                tool_id_list.append(tool_call_id)
            function_name = "end_call"
            arguments = json.dumps(
                {"reason": call.reason, "goodbye": call.goodbye},
                ensure_ascii=False,
            )
            tool_call_id = "textual-end-call"

        if self._call_session is not None and self._call_session.llm_turns == 1:
            from fernruf.voice.must_say import supplement_must_say

            before = self._call_session.llm_completion_text
            updated = supplement_must_say(before, self._call_session.phase.must_say)
            if updated != before:
                addition = updated[len(before) :].strip()
                self._call_session.llm_completion_text = updated
                if addition and not self._call_session.phase.suppresses_llm:
                    LOG.info("Speaking must_say the model left out (%s chars)", len(addition))
                    await self._push_llm_text(addition)
        if self._call_session is not None:
            self._call_session.phase.note_assistant_completion(
                self._call_session.llm_completion_text
            )

        if not first_liveness_seen:
            if (
                self._completion_skipped
                or finish_reason == "stop"
                or (
                    self._call_session is not None
                    and self._call_session.phase.suppresses_llm
                )
            ):
                LOG.info(
                    "LLM completed with nothing to say model=%s finish_reason=%s skipped=%s",
                    self._logged_model(),
                    finish_reason,
                    self._completion_skipped,
                )
                return
            LOG.warning(
                "LLM stream ended without speakable, tool, or reasoning delta model=%s",
                self._logged_model(),
            )
            raise asyncio.TimeoutError

        if spoken_len:
            LOG.info(
                "LLM response text_len=%s reasoning_len=%s finish_reason=%s model=%s",
                spoken_len,
                reasoning_len,
                finish_reason,
                self._logged_model(),
            )
        elif reasoning_len and not functions_list:
            LOG.warning(
                "LLM reasoning-only stream (%s chars), no speakable content "
                "(model=%s finish_reason=%s)",
                reasoning_len,
                self._logged_model(),
                finish_reason,
            )
        elif not function_name and not functions_list:
            LOG.warning(
                "LLM empty speakable content (model=%s finish_reason=%s)",
                self._logged_model(),
                finish_reason,
            )

        if function_name:
            functions_list.append(function_name)
            arguments_list.append(arguments or "{}")
            tool_id_list.append(tool_call_id)

            function_calls: list[FunctionCallFromLLM] = []
            for fn, args, tool_id in zip(functions_list, arguments_list, tool_id_list):
                try:
                    parsed = json.loads(args)
                except json.JSONDecodeError:
                    LOG.warning("%s: Failed to parse function call arguments: %s", self, args)
                    continue
                function_calls.append(
                    FunctionCallFromLLM(
                        context=context,
                        tool_call_id=tool_id,
                        function_name=fn,
                        arguments=parsed,
                    )
                )
            if function_calls:
                deduped: list[FunctionCallFromLLM] = []
                seen_ids: set[str] = set()
                seen_keys: set[tuple[str, str]] = set()
                for fc in function_calls:
                    tool_id = fc.tool_call_id or ""
                    key = (fc.function_name, json.dumps(fc.arguments, sort_keys=True))
                    if tool_id and tool_id in seen_ids:
                        LOG.warning("Skipping duplicate tool_call_id %s", tool_id)
                        continue
                    if key in seen_keys:
                        LOG.warning("Skipping duplicate function call %s", fc.function_name)
                        continue
                    if tool_id:
                        seen_ids.add(tool_id)
                    seen_keys.add(key)
                    deduped.append(fc)
                await self.run_function_calls(deduped)
        await self._speak_pending_reask()
