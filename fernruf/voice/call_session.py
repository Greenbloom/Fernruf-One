from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fernruf.settings import Settings
from fernruf.voice.call_phase import CallPhase


def message_text(msg: Any) -> str:
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
            return " ".join(parts).strip()
    role = getattr(msg, "role", None)
    if role == "user":
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content.strip()
    return ""


def coerce_tool_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""


@dataclass
class M2CallSession:
    """Per-call bookkeeping. End-call decisions live on ``phase`` only."""

    phase: CallPhase = field(default_factory=CallPhase)
    user_turn_count: int = 0
    call_started_at: float = field(default_factory=time.monotonic)
    assistant_turn_text: str = ""
    llm_completion_text: str = ""
    last_user_text: str = ""
    assistant_turns: list[str] = field(default_factory=list)
    llm_in_flight: bool = False
    llm_turns: int = 0
    pending_user_merge: list[str] = field(default_factory=list)
    observed_user_stt_turns: int = 0
    context_messages: list[Any] = field(default_factory=list)
    handled_tool_call_ids: set[str] = field(default_factory=set)
    last_user_turn_stop_mono: float | None = None
    last_bot_stopped_speaking_mono: float | None = None
    filler_played_this_turn: bool = False
    filler_played_last_turn_without_answer: bool = False
    speakable_llm_this_turn: bool = False
    speakable_llm_last_turn: bool = False
    llm_used_fallback: bool = False
    answered_mono: float | None = None
    transcript_lines: list[dict[str, Any]] = field(default_factory=list)
    # Spoken assistant text collected by the pipeline but not yet committed.
    # BotStoppedSpeaking is the usual commit point; the final end_call turn
    # often hangs up on QUEUE_DRAINED before that frame is processed.
    uncommitted_agent_text: str = ""
    spoken_goodbye: str = ""

    @property
    def pending_hangup(self) -> bool:
        return self.phase.is_ending

    @property
    def hung_up(self) -> bool:
        return self.phase.is_hung_up

    @property
    def closing(self) -> bool:
        return self.phase.is_ending

    @property
    def end_call_accepted(self) -> bool:
        return self.phase.is_ending or self.phase.is_hung_up

    @property
    def callee_goodbye_seen(self) -> bool:
        return self.phase.callee_said_goodbye

    @property
    def callee_requested_end(self) -> bool:
        return self.phase.callee_said_goodbye

    @property
    def pending_tool_tts(self) -> list[str]:
        text = self.phase.goodbye_tts.strip()
        return [text] if text else []

    @property
    def pending_hangup_reason(self) -> str:
        return self.phase.hangup_reason

    def _opener_spoken(self) -> bool:
        if self.assistant_turns or self.assistant_turn_text.strip():
            return True
        for msg in self.context_messages:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "assistant" and message_text(msg):
                return True
        return False

    @staticmethod
    def count_completed_exchanges(messages: list[Any]) -> int:
        """Count user replies that follow at least one assistant message."""
        seen_assistant = False
        exchanges = 0
        for msg in messages or []:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "assistant":
                seen_assistant = True
            elif role == "user" and seen_assistant:
                if message_text(msg):
                    exchanges += 1
        return exchanges

    def mark_answered(self, mono: float | None = None) -> None:
        if self.answered_mono is None:
            self.answered_mono = time.monotonic() if mono is None else mono

    def ms_since_answer(self) -> int:
        anchor = self.answered_mono if self.answered_mono is not None else self.call_started_at
        return max(0, int((time.monotonic() - anchor) * 1000))

    def note_transcript_line(self, speaker: str, text: str) -> None:
        """Record one spoken line. A repeated consecutive line is the same utterance."""
        text = text.strip()
        if not text or speaker not in ("agent", "callee"):
            return
        if self.transcript_lines:
            last = self.transcript_lines[-1]
            if last["speaker"] == speaker and last["text"] == text:
                return
            t_ms = max(self.ms_since_answer(), int(last["t_ms"]))
        else:
            t_ms = self.ms_since_answer()
        self.transcript_lines.append({"t_ms": t_ms, "speaker": speaker, "text": text})

    def observe_committed_lines(self, pairs: list[tuple[str, str]]) -> None:
        """Append context lines that are not already in the spoken transcript."""
        idx = 0
        lines = self.transcript_lines
        for speaker, text in pairs:
            text = text.strip()
            if not text:
                continue
            if idx < len(lines) and lines[idx]["speaker"] == speaker and lines[idx]["text"] == text:
                idx += 1
                continue
            if (
                idx > 0
                and lines[idx - 1]["speaker"] == speaker
                and lines[idx - 1]["text"] == text
            ):
                continue
            if idx < len(lines):
                return
            self.note_transcript_line(speaker, text)
            idx += 1

    def flush_unrecorded_assistant(self, text: str) -> None:
        """Keep a spoken end_call turn that never got another user snapshot."""
        text = (text or "").strip()
        if not text:
            return
        for line in reversed(self.transcript_lines):
            if line["speaker"] == "callee":
                break
            if line["speaker"] == "agent":
                return
        self.note_transcript_line("agent", text)

    def ensure_agent_line(self, text: str) -> None:
        """Append ``text`` when the current turn's agent tail does not already include it."""
        text = (text or "").strip()
        if not text:
            return
        for line in reversed(self.transcript_lines):
            if line["speaker"] == "callee":
                break
            if line["speaker"] != "agent":
                continue
            existing = str(line["text"])
            if existing == text or text in existing:
                return
        self.note_transcript_line("agent", text)

    def append_final_spoken_lines(self) -> None:
        """Keep the last spoken agent turn when the commit frame never arrived."""
        self.ensure_agent_line(self.uncommitted_agent_text)
        goodbye = (self.spoken_goodbye or "").strip()
        if not goodbye and self.phase.is_hung_up:
            goodbye = (self.phase.goodbye_tts or "").strip()
        self.ensure_agent_line(goodbye)

    def note_user_transcript(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.last_user_text = text
        self.observed_user_stt_turns += 1
        self.user_turn_count = max(self.user_turn_count, self.observed_user_stt_turns)
        self.phase.on_user_turn(
            text,
            turn_index=self.user_turn_count,
            opener_spoken=self._opener_spoken(),
        )

    def sync_from_llm_messages(self, messages: list[Any]) -> None:
        """User-turn count and callee goodbye from committed context messages."""
        self.context_messages = list(messages or [])
        user_texts: list[str] = []
        for msg in messages or []:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role != "user":
                continue
            t = message_text(msg)
            if t:
                user_texts.append(t)
        if user_texts:
            self.last_user_text = user_texts[-1]
        self.user_turn_count = max(
            self.count_completed_exchanges(messages),
            len(user_texts),
            self.observed_user_stt_turns,
        )
        opener = self._opener_spoken()
        for t in user_texts:
            self.phase.note_callee_text(t, opener_spoken=opener)
        if user_texts:
            self.phase.on_user_turn(
                user_texts[-1],
                turn_index=self.user_turn_count,
                opener_spoken=opener,
            )

    def note_assistant_text(self, chunk: str) -> None:
        self.assistant_turn_text += chunk

    def reset_assistant_turn(self) -> None:
        self.assistant_turn_text = ""
        self.llm_completion_text = ""

    def reset_llm_completion(self) -> None:
        self.llm_completion_text = ""

    def append_llm_completion(self, chunk: str) -> None:
        if chunk:
            self.llm_completion_text += chunk

    def commit_assistant_turn(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.assistant_turns.append(text)
        if len(self.assistant_turns) > 2:
            self.assistant_turns = self.assistant_turns[-2:]
        self.assistant_turn_text = text
        self.phase.note_assistant_completion(text)

    def in_flight_assistant_completion(self) -> str:
        completion = self.llm_completion_text.strip()
        if completion:
            return completion
        return self.assistant_turn_text.strip()

    def validate_end_call(
        self,
        reason: str,
        settings: Settings,
        *,
        goodbye: Any = "",
    ) -> str | None:
        del settings, goodbye
        text = self.in_flight_assistant_completion()
        if text:
            self.phase.note_assistant_completion(text)
        if self.phase.is_hung_up or self.phase.is_ending:
            return None
        if not coerce_tool_str(reason):
            return "end_call blocked: provide a short reason."
        if self.phase.question_open and not self.phase.callee_may_end_early():
            return (
                "end_call blocked: assistant asked a question in this turn; "
                "wait for the callee to answer before ending the call."
            )
        if self.phase.missing_must_say() and not self.phase.callee_may_end_early():
            return (
                "end_call blocked: must_say has not been spoken yet. "
                "State it before ending, unless the callee hangs up or refuses."
            )
        if self.phase.callee_said_goodbye or self.phase.callee_refused:
            return None
        if not self.phase.callee_spoke_since_question:
            return (
                "end_call blocked: wait until the callee answers or says goodbye."
            )
        if self.phase.should_reask():
            self.phase.pending_reask = True
            self.phase.reask_used = True
            return (
                "end_call blocked: the answer is unclear or missing; "
                "ask once more before ending."
            )
        return None
