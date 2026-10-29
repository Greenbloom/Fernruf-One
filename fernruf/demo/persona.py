"""Default callee role for the local demo."""

from __future__ import annotations

from fernruf.settings import Settings

DEFAULT_DEMO_REQUEST = "Passt Dienstag für einen Termin?"

DEFAULT_DEMO_TASK = (
    "Die Frage, ob Dienstag passt, wurde schon im Gesprächseinstieg gestellt. "
    "Warte auf die Antwort der Praxis. Sobald sie zustimmt, sofort end_call "
    "mit einem kurzen Abschied. Keine weitere Frage."
)

DEFAULT_DEMO_PERSONA = (
    "Du bist die Rezeption der fiktiven Praxis Beispiel. "
    "Warte auf die Frage, ob Dienstag passt. "
    "Antworte dann in einem Satz: Dienstag um zehn Uhr passt."
)

DEFAULT_MAX_SECONDS = 30


class DemoVoiceError(RuntimeError):
    """demo-call has no public library voice, or a voice matches the operator."""


def remember_operator_voice(settings: Settings) -> None:
    """Keep the original ``FISH_VOICE_ID`` so a later voice pin cannot hide it."""
    if getattr(settings, "_operator_fish_voice_id", None) is None:
        object.__setattr__(
            settings,
            "_operator_fish_voice_id",
            (settings.fish_voice_id or "").strip(),
        )


def require_demo_voices(settings: Settings) -> tuple[str, str]:
    """Caller and callee Fish voices. Never ``FISH_VOICE_ID``."""
    remember_operator_voice(settings)
    caller = (settings.demo_caller_voice_id or "").strip()
    callee = (settings.demo_callee_voice_id or "").strip()
    operator = str(getattr(settings, "_operator_fish_voice_id", "") or "").strip()
    missing = [
        name
        for name, value in (
            ("FERNRUF_DEMO_CALLER_VOICE_ID", caller),
            ("FERNRUF_DEMO_CALLEE_VOICE_ID", callee),
        )
        if not value
    ]
    if missing:
        raise DemoVoiceError(
            "demo-call requires "
            + " and ".join(missing)
            + ". Set FERNRUF_DEMO_CALLER_VOICE_ID and FERNRUF_DEMO_CALLEE_VOICE_ID "
            "to public Fish Audio library voices. "
            "demo-call does not fall back to FISH_VOICE_ID."
        )
    clashes = [
        name
        for name, value in (
            ("FERNRUF_DEMO_CALLER_VOICE_ID", caller),
            ("FERNRUF_DEMO_CALLEE_VOICE_ID", callee),
        )
        if operator and value == operator
    ]
    if clashes:
        verb = "it matches" if len(clashes) == 1 else "they match"
        raise DemoVoiceError(
            "demo-call refuses "
            + " and ".join(clashes)
            + f" because {verb} FISH_VOICE_ID. "
            "Use public Fish Audio library voices, not the operator voice or a personal clone."
        )
    return caller, callee


def pin_demo_caller_voice(settings: Settings, caller_voice_id: str) -> Settings:
    """Speak the demo with this voice, including after language setup."""
    voice = (caller_voice_id or "").strip()
    pinned = settings.model_copy(update={"fish_voice_id": voice})
    object.__setattr__(pinned, "_pinned_fish_voice_id", voice)
    return pinned


def apply_demo_caller_voice(settings: Settings) -> Settings:
    caller, _callee = require_demo_voices(settings)
    return pin_demo_caller_voice(settings, caller)


def spoken_demo_request(task: str) -> str:
    """The sentence Fernruf speaks in the opening, after the AI notice."""
    text = (task or "").strip()
    if not text or text == DEFAULT_DEMO_TASK:
        return DEFAULT_DEMO_REQUEST
    for part in text.replace("!", ".").split("."):
        sentence = part.strip()
        if sentence.endswith("?"):
            return sentence
    return text


def callee_system_prompt(persona: str) -> str:
    role = (persona or "").strip() or DEFAULT_DEMO_PERSONA
    return (
        "Du bist die angerufene Seite eines Telefonats. "
        "Schweige, solange die anrufende Seite nur sagt, dass sie ein KI-Assistent ist. "
        "Antworte erst, wenn du eine konkrete Frage gehört hast. "
        "Sprich ausschließlich in der folgenden Rolle. "
        "Keine Aufzählungen, ein oder zwei Sätze.\n\n"
        f"{role}"
    )
