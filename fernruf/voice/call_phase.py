"""One owner for where a call is, and whether it may end.

Phases: opening -> talking -> ending -> hung_up.
Processors read this object. They do not keep a second copy of
question or goodbye state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_BOT_GOODBYE = re.compile(
    r"(auf\s+wiederh|wiederhör|\bwiederhören\b|tschüss|tschuss|"
    r"\bgood-?bye\b|\bbye\b|再见|拜拜)",
    re.IGNORECASE,
)
_CALLEE_GOODBYE = re.compile(
    r"(auf\s+wiederh|wiederhör|\bwiederhören\b|wieder\s*sehen|"
    r"tschüss|tschuss|ciao|\bgood-?bye\b|\bbye\b|das\s+war['']s|"
    r"再见|拜拜)",
    re.IGNORECASE,
)
_CALLEE_END = re.compile(
    r"(du\s+kannst(?:\s+es)?\s+aufleg|"
    r"\bleg\s+auf\b|"
    r"leg(?:en)?\s+(?:auf|das\s+telefon\s+auf)|"
    r"\bauflegen\b|"
    r"beenden\s+(?:wir|den\s+anruf)|"
    r"reicht\s+(?:mir|so)|"
    r"kein\s+problem.*aufleg|"
    r"tschüss|tschuss)",
    re.IGNORECASE,
)

# Direct "?" plus indirect German asks. A purpose clause such as
# "prüfen, ob alles funktioniert" is not an ask; "wollte fragen, ob …" is.
_ASK_RE = re.compile(
    r"""(?ix)
    (?:
        \b(?:wollte|möchte|moechte|mag|würde|wuerde|hätte|haette)
        (?:\s+\w+){0,8}?
        \s+(?:fragen|wissen|erfahren|klären|klaeren)\b
      | \b(?:frage|fragen|gefragt|wissen)\b[^.\n]{0,80}?
        \b(?:ob|wann|wie|wo|wohin|woher|warum|weshalb|wieso|wer|was|welche\w*)\b
      | (?<!als\s)\bob\s+
        (?:sie|du|ihr|er|es|man|wir|ich|der|die|das|herr|frau)\b
      | \b(?:könnten|koennten|könntest|koenntest|können|koennen|kannst|
           würden|wuerden|würdest|wuerdest|
           hätten|haetten|hättest|haettest|
           sollten|solltest|
           möchten|moechten|möchtest|moechtest|
           dürfen|duerfen|dürfte|duerfte|darf)
        \s+(?:sie|du|ihr)\b
      | (?:^|[.!;]\s+)
        (?:
            (?:wer|was|wann|wo|wohin|woher|warum|weshalb|wieso|
               welche[rnms]?|welchem|welchen|wem|wen|wessen)\b
          | wie\b(?!\s+(?:besprochen|gesagt|vereinbart|erwähnt|erwaehnt|geplant|abgesprochen)\b)
        )
    )
    """
)


def assistant_turn_asks(text: str) -> bool:
    """True when this assistant turn asks or requests something.

    A question mark is enough. So are indirect German asks without one
    (``wollte fragen, ob``, ``möchte wissen, wann``, ``ob Sie``, ``könnten Sie``,
    and sentence-initial w-words). Message delivery with no ask is false.
    """
    text = (text or "").strip()
    if not text:
        return False
    if "?" in text:
        return True
    return _ASK_RE.search(text) is not None


class Phase(str, Enum):
    OPENING = "opening"
    TALKING = "talking"
    ENDING = "ending"
    HUNG_UP = "hung_up"


def _norm_farewell(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    return collapsed.strip(" \t.!?,;:。！？…")


def farewell_in_text(text: str, phrase: str = "") -> bool:
    """True when ``text`` already contains a farewell in this call's language.

    German markers stay as they were. English ``Goodbye``, Chinese ``再见``,
    and the call's own goodbye phrase match the same way, so a spoken farewell
    is not spoken again by ``end_call``.
    """
    if _BOT_GOODBYE.search(text or ""):
        return True
    needle = _norm_farewell(phrase)
    if len(needle) < 2:
        return False
    return needle in _norm_farewell(text)


_DE_FUNC = frozenset(
    "der die das den dem des ein eine einer einem einen ich du er sie es wir ihr "
    "ist sind war hast habe hat nicht mit auf von für zu im am".split()
)
_EN_FUNC = frozenset(
    "the a an is are was were you i to for of that this with have has it we they "
    "my your do did".split()
)
_YES_NO = re.compile(
    r"(?i)\b(yes|yeah|yep|no|nope|ok|okay|sure|ja|nein|doch|genau|stimmt|klar|passt|gerne)\b"
)
_YES_NO_CJK = ("是的", "不是", "好的", "可以", "对的")
_ONLY_FILLER = re.compile(
    r"(?ix)^(?:"
    r"hallo|hello|hi|hey|guten\s+(?:tag|morgen|abend)|"
    r"good\s+(?:morning|afternoon|evening)|你好|您好|喂|"
    r"mhm+|hm+|uh+|um+|äh+|ah+|pardon|wie\s+bitte|what|excuse\s+me|sorry|"
    r"请再说一遍|什么"
    r")[\s!.?。]*$"
)
# Bare thanks is not a farewell and not an answer. Whisper emits it on silence.
_ONLY_THANKS = re.compile(
    r"(?ix)^(?:(?:"
    r"vielen\s+dank|danke(?:\s+schön|\s+schoen)?|"
    r"thanks(?:\s+for\s+watching)?|thank\s+you(?:\s+for\s+watching)?"
    r")[\s!.?,]*)+$"
)
_CALLEE_REFUSAL = re.compile(
    r"(?i)("
    r"\bnot interested\b|\bdon't call\b|\bdo not call\b|\bstop calling\b|"
    r"\bleave me alone\b|"
    r"kein interesse|nicht interessiert|rufen sie (?:mich )?nicht|"
    r"lass(?:en)? sie mich|hör(?:en)? auf|hoer(?:en)? auf"
    r")"
)


def _latin_tokens(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß]+", (text or "").casefold())


def answer_is_unclear(text: str, *, call_language: str) -> bool:
    """True when this callee turn does not contain a usable answer.

    A yes, no, number, farewell, or bare greeting counts as not blocking.
    A transcript in the wrong language (German STT on an English call) does
    not answer the question, so the bot asks once more instead of ending.
    """
    text = (text or "").strip()
    if not text:
        return True
    if _ONLY_THANKS.match(text):
        return True
    if farewell_in_text(text) or _ONLY_FILLER.match(text):
        return False
    if _YES_NO.search(text) or any(tok in text for tok in _YES_NO_CJK):
        return False
    if re.search(r"\d", text):
        return False
    lang = (call_language or "de").split("-", 1)[0]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-zÄÖÜäöüß]", text))
    if lang == "zh":
        return latin_chars >= 8 and cjk == 0
    if cjk >= 2 and latin_chars == 0:
        return True
    if lang not in ("de", "en"):
        return False
    tokens = _latin_tokens(text)
    german = sum(1 for tok in tokens if tok in _DE_FUNC)
    english = sum(1 for tok in tokens if tok in _EN_FUNC)
    if lang == "en":
        return german >= 2 and english == 0
    return english >= 2 and german == 0


def _misheard_bye(text: str, *, opener_spoken: bool) -> bool:
    """STT often hears the opener as 'Bye.' — that is not a farewell."""
    if not opener_spoken:
        return False
    return bool(re.match(r"^bye\.?$", text.strip(), re.IGNORECASE))


@dataclass
class CallPhase:
    """Call phase plus the only question / goodbye bits end_call needs."""

    name: Phase = Phase.OPENING
    question_open: bool = False
    callee_spoke_since_question: bool = False
    callee_said_goodbye: bool = False
    apology_played: bool = False
    hangup_reason: str = ""
    goodbye_tts: str = ""
    spoken_text: str = ""
    await_playback: bool = False
    user_turns_seen: int = 0
    call_language: str = "de"
    goodbye_phrase: str = ""
    must_say: tuple[str, ...] = ()
    goal_needs_answer: bool = False
    agent_spoken: str = ""
    callee_refused: bool = False
    last_user_text: str = ""
    awaiting_answer: bool = False
    reask_used: bool = False
    pending_reask: bool = False
    reask_spoken: bool = False

    @property
    def is_ending(self) -> bool:
        return self.name is Phase.ENDING

    @property
    def is_hung_up(self) -> bool:
        return self.name is Phase.HUNG_UP

    @property
    def suppresses_llm(self) -> bool:
        """Ending and hung up: no LLM, no filler, no apology, no rerun."""
        return self.name in (Phase.ENDING, Phase.HUNG_UP)

    def note_callee_text(self, text: str, *, opener_spoken: bool) -> None:
        if self.suppresses_llm:
            return
        text = (text or "").strip()
        if not text or _misheard_bye(text, opener_spoken=opener_spoken):
            return
        if _CALLEE_REFUSAL.search(text):
            self.callee_refused = True
        if _CALLEE_GOODBYE.search(text) or _CALLEE_END.search(text):
            self.callee_said_goodbye = True

    def callee_may_end_early(self) -> bool:
        """Clear hangup or refusal. Bare thanks is neither."""
        return self.callee_said_goodbye or self.callee_refused

    def missing_must_say(self) -> list[str]:
        from fernruf.voice.must_say import missing_must_say

        return missing_must_say(self.agent_spoken, self.must_say)

    def on_user_turn(self, text: str, *, turn_index: int, opener_spoken: bool) -> None:
        """A user turn clears an open question. Goodbye sticks."""
        if self.suppresses_llm:
            return
        text = (text or "").strip()
        if not text:
            return
        self.note_callee_text(text, opener_spoken=opener_spoken)
        self.last_user_text = text
        if turn_index <= self.user_turns_seen:
            return
        self.user_turns_seen = turn_index
        if self.name is Phase.OPENING:
            self.name = Phase.TALKING
        self.question_open = False
        self.callee_spoke_since_question = True
        # A greeting is not the answer, and it does not use up the one retry.
        if (
            self.awaiting_answer
            and not _ONLY_FILLER.match(text)
            and not answer_is_unclear(text, call_language=self.call_language)
        ):
            self.awaiting_answer = False

    def should_reask(self) -> bool:
        """One retry when the open question got no usable answer."""
        if self.reask_used or self.reask_spoken or not self.awaiting_answer:
            return False
        return answer_is_unclear(self.last_user_text, call_language=self.call_language)

    def note_assistant_completion(self, text: str) -> None:
        """Question state comes from this completion only.

        A farewell spoken as plain text, after the callee has answered or
        said goodbye, is an end_call with no extra goodbye line.
        """
        if self.suppresses_llm:
            return
        text = (text or "").strip()
        if not text:
            return
        if self.name is Phase.OPENING:
            self.name = Phase.TALKING
        spoken = self.agent_spoken.casefold()
        needle = text.casefold()
        if spoken == needle or spoken.endswith(" " + needle):
            return
        had_must_say = not self.missing_must_say() if self.must_say else True
        self.agent_spoken = f"{self.agent_spoken} {text}".strip()
        asks = assistant_turn_asks(text)
        if asks:
            self.question_open = True
            self.callee_spoke_since_question = False
            self.awaiting_answer = True
        else:
            self.question_open = False
        just_delivered = bool(self.must_say) and not had_must_say and not self.missing_must_say()
        if just_delivered and self.goal_needs_answer:
            self.question_open = True
            self.awaiting_answer = True
            self.callee_spoke_since_question = False
        if farewell_in_text(text, self.goodbye_phrase) and self.accepts_end_call():
            if self.missing_must_say() and not self.callee_may_end_early():
                return
            if self.should_reask():
                self.pending_reask = True
                return
            self.begin_ending(
                reason="spoken farewell",
                goodbye="",
                spoken=text,
                await_playback=True,
            )

    def accepts_end_call(self) -> bool:
        """Callee has spoken since the last ask, or said goodbye and nothing is open.

        An ask in the current assistant turn blocks ending until the callee
        speaks again. A goodbye from before that ask does not answer it.
        Message delivery (no ask in the turn) can still end on this turn.
        """
        if self.is_ending or self.is_hung_up:
            return True
        if self.question_open:
            return False
        if self.callee_said_goodbye:
            return True
        return self.callee_spoke_since_question

    def begin_ending(
        self,
        *,
        reason: str,
        goodbye: str = "",
        spoken: str = "",
        await_playback: bool = False,
    ) -> None:
        if self.name is Phase.HUNG_UP:
            return
        if self.name is not Phase.ENDING:
            self.name = Phase.ENDING
            self.hangup_reason = (reason or "end_call").strip() or "end_call"
            self.await_playback = await_playback or bool(goodbye)
        if spoken and not self.spoken_text:
            self.spoken_text = spoken
        if goodbye and not self.goodbye_tts:
            self.goodbye_tts = goodbye
            self.await_playback = True

    def mark_hung_up(self) -> None:
        self.name = Phase.HUNG_UP

    def note_apology(self) -> bool:
        """True only the first time a real stall may speak the apology."""
        if self.suppresses_llm or self.apology_played:
            return False
        self.apology_played = True
        return True
