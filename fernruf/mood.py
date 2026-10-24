"""Per-turn callee mood, scored beside the chat LLM and applied only at Fish.

The chat model never sees mood labels or bracket tags. A TypeSafe Jev request
starts when the final user transcript is ready and runs concurrently with the
reply. The first Fish segment of that reply may gain one inline tag. If Jev is
late, missing, or below the confidence bar, the segment is spoken as written.
TTS does not wait.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from fernruf.lang_names import language_name
from fernruf.settings import Settings
from fernruf.tts.rest_stream import _should_cache_phrase
from fernruf.voice.call_session import M2CallSession, message_text

LOG = logging.getLogger("fernruf.mood")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
MOOD_INSTRUCTIONS = "Stimmung der angerufenen Person in ihrer letzten Aussage am Telefon"

# Choice criteria sent to Jev. The chat LLM is not part of this request.
MOOD_CRITERIA: dict[str, str] = {
    "freundlich": "Freundlich, warm, entgegenkommend oder erfreut.",
    "neutral": "Sachlich oder ohne erkennbare Emotion.",
    "genervt": "Genervt, ungeduldig, gereizt oder abweisend.",
    "traurig": "Traurig, enttäuscht, bedrückt oder niedergeschlagen.",
    "belustigt": "Amüsiert, lachend oder scherzhaft.",
    "misstrauisch": "Misstrauisch, skeptisch oder vorsichtig gegenüber dem Anrufer.",
    "gestresst": "Gestresst, gehetzt, überfordert oder unter Zeitdruck.",
}

# Fish S2 inline tags. Neutral and unknown choices add nothing.
# belustigt is [amused]; switch this one value to [chuckling] if that reads better.
MOOD_FISH_TAGS: dict[str, str] = {
    "freundlich": "[warm]",
    "belustigt": "[amused]",
    "traurig": "[empathetic, soft voice]",
    "genervt": "[calm]",
    "misstrauisch": "[calm, reassuring]",
    "gestresst": "[calm]",
    "neutral": "",
}

_FAREWELL = re.compile(
    r"(auf\s+wiederh|wiederhör|wiederhoren|tschüss|tschuss|"
    r"\bgoodbye\b|\bbye\b|再见|拜拜)",
    re.IGNORECASE,
)


class _Once:
    def __init__(self) -> None:
        self.done = False

    def log(self, level: str, message: str, *args: object) -> None:
        if self.done:
            return
        self.done = True
        getattr(LOG, level)(message, *args)


_missing_key = _Once()
_disabled = _Once()
_unsupported = _Once()
_fault = _Once()
_auth_failed = False


def reset_mood_notices() -> None:
    """Test helper. Production logs each silent-off reason once per process."""
    global _auth_failed
    _auth_failed = False
    for notice in (_missing_key, _disabled, _unsupported, _fault):
        notice.done = False


def fish_model_supports_inline_tags(model: str) -> bool:
    """Fish S2 and S2.1 speak bracket tags. Older Fish models do not."""
    return (model or "").strip().lower().startswith("s2")


def _call_lang(code: str | None) -> str:
    return (code or "de").strip().lower() or "de"


def mood_instructions(call_language: str = "de") -> str:
    """German criteria stay the choice keys. Non-German calls say so in the instructions."""
    lang = _call_lang(call_language)
    if lang == "de":
        return MOOD_INSTRUCTIONS
    name = language_name(lang)
    return (
        f"{MOOD_INSTRUCTIONS} "
        f"Das Gespräch läuft auf {name} ({lang}); die Aussage kann auf {name} sein. "
        "Bewerte die Stimmung trotzdem und antworte mit einem der deutschen Kriterien."
    )


def mood_state(
    utterance: str,
    prior_bot: str | None,
    *,
    call_language: str = "de",
) -> str:
    utterance = " ".join((utterance or "").split())
    prior = " ".join((prior_bot or "").split())
    if not prior:
        body = utterance
    else:
        body = (
            "Letzte Äußerung des Assistenten: "
            f"{prior}\nLetzte Aussage der angerufenen Person: {utterance}"
        )
    lang = _call_lang(call_language)
    if lang == "de":
        return body
    name = language_name(lang)
    return f"Gesprächssprache: {name} ({lang}).\n{body}"


def build_mood_request(
    utterance: str,
    prior_bot: str | None,
    *,
    model: str,
    call_language: str = "de",
) -> dict[str, Any]:
    return {
        "model": model,
        "state": mood_state(utterance, prior_bot, call_language=call_language),
        "questions": {
            "mood": {
                "type": "choice",
                "instructions": mood_instructions(call_language),
                "criteria": dict(MOOD_CRITERIA),
            }
        },
    }


def fish_tag_for(
    choice: str,
    confidence: float,
    *,
    min_confidence: float,
    fish_model: str,
) -> str:
    if not fish_model_supports_inline_tags(fish_model):
        return ""
    if confidence < min_confidence:
        return ""
    return MOOD_FISH_TAGS.get((choice or "").strip().lower(), "")


@dataclass(frozen=True)
class MoodReading:
    choice: str
    confidence: float
    latency_ms: float | None = None
    timed_out: bool = False
    error: str = ""


class MoodScorer(Protocol):
    async def detect(self, utterance: str, *, prior_bot: str | None = None) -> MoodReading: ...


def _neutral(*, latency_ms: float | None, timed_out: bool = False, error: str = "") -> MoodReading:
    return MoodReading(
        choice="neutral",
        confidence=0.0,
        latency_ms=latency_ms,
        timed_out=timed_out,
        error=error,
    )


def parse_jev_choice(payload: Any, question: str, *, default: str = "") -> tuple[str, float]:
    """Read ``answers.<question>.choice`` and ``.confidence`` from a systemone body."""
    if not isinstance(payload, dict):
        return default, 0.0
    answers = payload.get("answers")
    node = answers.get(question) if isinstance(answers, dict) else None
    if not isinstance(node, dict):
        return default, 0.0
    choice = str(node.get("choice") or node.get("value") or default).strip().lower()
    try:
        confidence = float(node.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence > 1.0 and confidence <= 100.0:
        confidence /= 100.0
    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = 1.0
    return choice or default, confidence


def _parse_choice(payload: Any) -> tuple[str, float]:
    return parse_jev_choice(payload, "mood", default="neutral")


def jev_auth_is_blocked() -> bool:
    return _auth_failed


def mark_jev_auth_failed() -> None:
    global _auth_failed
    _auth_failed = True


_shared_http: httpx.AsyncClient | None = None


def _shared_mood_http() -> httpx.AsyncClient:
    """One connection pool for the process. Per-call timeout is wait_for, not this."""
    global _shared_http
    if _shared_http is None or _shared_http.is_closed:
        _shared_http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=0.3))
    return _shared_http


def shared_jev_http() -> httpx.AsyncClient:
    """Process-wide systemone client. Mood and call-language detection share it."""
    return _shared_mood_http()


async def post_systemone(
    *,
    api_key: str,
    body: dict[str, Any],
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """POST ``/v1/systemone``. A transport builds a short-lived client for tests."""
    owns = False
    if client is None:
        if transport is not None:
            client = httpx.AsyncClient(transport=transport)
            owns = True
        else:
            client = shared_jev_http()
    try:
        return await asyncio.wait_for(
            client.post(
                JEV_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ),
            timeout=timeout_s,
        )
    finally:
        if owns:
            await client.aclose()


class JevMoodClient:
    """POST /v1/systemone. The chat provider and model id are not sent."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            if self._http is None or self._http.is_closed:
                self._http = httpx.AsyncClient(transport=self._transport)
            return self._http
        return _shared_mood_http()

    async def detect(self, utterance: str, *, prior_bot: str | None = None) -> MoodReading:
        started = time.monotonic()

        def elapsed() -> float:
            return (time.monotonic() - started) * 1000

        if jev_auth_is_blocked():
            return _neutral(latency_ms=0.0, error="auth")
        api_key = (self._settings.typesafe_api_key or "").strip()
        if not api_key:
            _missing_key.log(
                "info",
                "Callee mood detection off (TYPESAFE_API_KEY missing); replies stay neutral",
            )
            return _neutral(latency_ms=0.0, error="missing_key")
        timeout_s = max(self._settings.mood_timeout_ms, 0) / 1000
        if timeout_s <= 0:
            return _neutral(latency_ms=0.0, timed_out=True)
        body = build_mood_request(
            utterance,
            prior_bot,
            model=self._settings.mood_model,
            call_language=getattr(self._settings, "call_language", "") or "de",
        )
        try:
            response = await post_systemone(
                api_key=api_key,
                body=body,
                timeout_s=timeout_s,
                client=self._client(),
            )
        except TimeoutError:
            return _neutral(latency_ms=elapsed(), timed_out=True)
        except httpx.TimeoutException:
            return _neutral(latency_ms=elapsed(), timed_out=True)
        except httpx.HTTPError:
            _fault.log("warning", "Callee mood request failed; staying neutral")
            return _neutral(latency_ms=elapsed(), error="http")
        if response.status_code in (401, 403):
            mark_jev_auth_failed()
            _fault.log("warning", "Callee mood request was rejected; staying neutral")
            return _neutral(latency_ms=elapsed(), error="auth")
        if response.status_code >= 400:
            _fault.log(
                "warning",
                "Callee mood request failed (HTTP %s); staying neutral",
                response.status_code,
            )
            return _neutral(latency_ms=elapsed(), error=f"http_{response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            _fault.log("warning", "Callee mood response was not JSON; staying neutral")
            return _neutral(latency_ms=elapsed(), error="json")
        choice, confidence = _parse_choice(payload)
        return MoodReading(choice=choice, confidence=confidence, latency_ms=elapsed())


class MoodTurn:
    """One in-flight Jev score for the current user turn, and the Fish tag gate."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: M2CallSession | None = None,
        client: MoodScorer | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._client = client if client is not None else JevMoodClient(settings)
        self._task: asyncio.Task[None] | None = None
        self._gen = 0
        self._reply_gen = 0
        self._utterance = ""
        self._prior = ""
        self._reading: MoodReading | None = None
        self._decision: MoodReading | None = None
        self._settled = False
        self._armed = False
        self._direct_speak = False
        self._off = False
        self._tag_prefix = ""
        self._first_plain = ""
        self._started: float | None = None

    def start(self, utterance: str, *, prior_bot: str | None = None) -> None:
        utterance = " ".join((utterance or "").split())
        if not utterance:
            return
        if self._session is not None and self._session.phase.suppresses_llm:
            return
        prior_norm = " ".join((prior_bot or "").split())
        if utterance == self._utterance and (self._task is not None or self._off):
            # Keep the in-flight score. Restart only when assistant context
            # arrives while that score is still running and had no context yet.
            if (
                self._off
                or not prior_norm
                or prior_norm == self._prior
                or self._task is None
                or self._task.done()
            ):
                return
        self._cancel()
        self._utterance = utterance
        self._prior = prior_norm
        self._reading = None
        self._decision = None
        self._settled = False
        self._armed = False
        self._tag_prefix = ""
        self._first_plain = ""
        self._gen += 1
        self._started = time.monotonic()
        if not self._settings.mood_enabled or not self._settings.typesafe_requests_enabled():
            _disabled.log(
                "info",
                "Callee mood detection disabled (FERNRUF_MOOD_ENABLED=false or TypeSafe off)",
            )
            self._off = True
            return
        model = self._settings.effective_fish_model()
        if not fish_model_supports_inline_tags(model):
            _unsupported.log(
                "info",
                "Fish model %s has no inline emotion tags; callee mood stays neutral",
                model or "-",
            )
            self._off = True
            return
        if not (self._settings.typesafe_api_key or "").strip():
            _missing_key.log(
                "info",
                "Callee mood detection off (TYPESAFE_API_KEY missing); replies stay neutral",
            )
            self._off = True
            return
        self._off = False
        gen = self._gen
        self._task = asyncio.create_task(
            self._run(gen, utterance, prior_norm or None),
            name="fernruf-mood",
        )

    def begin_reply(self) -> None:
        self._reply_gen = self._gen
        self._armed = True
        self._tag_prefix = ""
        self._first_plain = ""
        self._settled = False

    def disarm_reply(self) -> None:
        self._armed = False

    def note_direct_speak(self) -> None:
        self._direct_speak = True

    def clear_direct_speak(self) -> None:
        self._direct_speak = False

    def decorate_segment(self, text: str) -> str:
        """Tag the first Fish segment of this reply, or return ``text`` unchanged.

        Never waits. A tag's own comma is applied after clause splitting, so it
        cannot become a second chunk. Cached phrases, the opener, fillers, and
        goodbyes are returned unchanged and do not use up the one tag.
        """
        raw = text or ""
        if not raw.strip() or not self._settings.mood_enabled or self._off:
            return raw
        if self._direct_speak or self._exempt(raw):
            return raw
        if not self._armed or self._reply_gen != self._gen:
            return raw
        if self._tag_prefix:
            if self._tag_prefix != "-" and self._same_opening(raw):
                return f"{self._tag_prefix} {raw.lstrip()}"
            return raw
        if not fish_model_supports_inline_tags(self._settings.effective_fish_model()):
            _unsupported.log(
                "info",
                "Fish model %s has no inline emotion tags; callee mood stays neutral",
                self._settings.effective_fish_model() or "-",
            )
            self._tag_prefix = "-"
            return raw
        self._first_plain = raw.strip()
        tag = self._resolve_tag()
        self._log_decision(tag)
        self._tag_prefix = tag or "-"
        if not tag:
            return raw
        return f"{tag} {raw.lstrip()}"

    async def _run(self, gen: int, utterance: str, prior_bot: str | None) -> None:
        try:
            reading = await self._client.detect(utterance, prior_bot=prior_bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            _fault.log("warning", "Callee mood request failed; staying neutral")
            reading = _neutral(latency_ms=self._elapsed(), error="error")
        if gen != self._gen or self._settled:
            return
        self._reading = reading

    def _resolve_tag(self) -> str:
        self._settled = True
        reading = self._reading
        if self._task is not None and not self._task.done():
            reading = None
        if reading is None:
            self._decision = _neutral(latency_ms=self._elapsed(), timed_out=True)
            return ""
        self._decision = reading
        return fish_tag_for(
            reading.choice,
            reading.confidence,
            min_confidence=self._settings.mood_min_confidence,
            fish_model=self._settings.effective_fish_model(),
        )

    def _log_decision(self, tag: str) -> None:
        reading = self._decision
        choice = reading.choice if reading else "neutral"
        confidence = reading.confidence if reading else 0.0
        latency = reading.latency_ms if reading is not None else None
        if latency is None:
            latency = self._elapsed()
        latency_text = "-" if latency is None else str(int(round(latency)))
        LOG.info(
            "mood=%s conf=%.1f tag=%s latency_ms=%s",
            choice,
            confidence,
            tag or "-",
            latency_text,
        )

    def _exempt(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if _should_cache_phrase(self._settings, stripped):
            return True
        if stripped == (self._settings.llm_delay_apology_phrase or "").strip():
            return True
        phrases = getattr(self._settings, "call_phrases", None) or {}
        goodbye = ""
        if isinstance(phrases, dict):
            goodbye = str(phrases.get("goodbye") or "").strip()
        if goodbye and stripped == goodbye:
            return True
        if _FAREWELL.search(stripped):
            return True
        session = self._session
        if session is None:
            return False
        if session.phase.is_ending or session.phase.is_hung_up:
            return True
        goodbye = (session.phase.goodbye_tts or "").strip()
        if goodbye and stripped == goodbye:
            return True
        if _FAREWELL.search(session.llm_completion_text or ""):
            return True
        return False

    def _same_opening(self, text: str) -> bool:
        plain = text.strip()
        first = self._first_plain
        if not plain or not first:
            return False
        return plain == first or first.startswith(plain) or plain.startswith(first)

    def _elapsed(self) -> float | None:
        if self._started is None:
            return None
        return (time.monotonic() - self._started) * 1000

    def _cancel(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()


class MoodTaggedWs:
    """Apply the mood tag on the text a live Fish socket sends, not on logs."""

    def __init__(self, inner: Any, prepare: Any) -> None:
        self._inner = inner
        self._prepare = prepare

    @property
    def fallback_rest(self) -> bool:
        return bool(getattr(self._inner, "fallback_rest", False))

    @fallback_rest.setter
    def fallback_rest(self, value: bool) -> None:
        setattr(self._inner, "fallback_rest", value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def stream_utterance(self, text: str) -> Any:
        async for item in self._inner.stream_utterance(self._prepare(text)):
            yield item


def prior_bot_line(session: M2CallSession) -> str | None:
    for msg in reversed(session.context_messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "assistant":
            continue
        text = message_text(msg)
        if text:
            return text
    if session.assistant_turns:
        return session.assistant_turns[-1]
    text = (session.assistant_turn_text or "").strip()
    return text or None


def last_user_and_prior(messages: list[Any]) -> tuple[str, str | None]:
    last_user = ""
    prior: str | None = None
    last_assistant: str | None = None
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        text = message_text(msg)
        if not text:
            continue
        if role == "assistant":
            last_assistant = text
        elif role == "user":
            last_user = text
            prior = last_assistant
    return last_user, prior
