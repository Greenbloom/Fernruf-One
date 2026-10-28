"""Default callee role for the local demo."""

from __future__ import annotations

DEFAULT_DEMO_PERSONA = (
    "Du bist die freundliche Rezeption der fiktiven Praxis Beispiel. "
    "Du sprichst Deutsch, höflich und kurz. "
    "Jemand ruft an, um einen Termin zu vereinbaren. "
    "Prüfe, ob der Dienstag passt. "
    "Dienstagvormittag ist frei. Wenn kein genauer Zeitpunkt genannt wird, "
    "schlage Dienstag um zehn Uhr vor und bestätige den Termin, "
    "sobald der Anrufer einverstanden ist."
)


def resolve_callee_voice(callee_voice_id: str, caller_voice_id: str) -> str:
    """Callee Fish voice, or the caller voice when the override is unset."""
    chosen = (callee_voice_id or "").strip()
    if chosen:
        return chosen
    return (caller_voice_id or "").strip()


def callee_system_prompt(persona: str) -> str:
    role = (persona or "").strip() or DEFAULT_DEMO_PERSONA
    return (
        "Du bist die angerufene Seite eines Telefonats. "
        "Warte, bis die anrufende Seite ausgesprochen hat, und antworte dann. "
        "Sprich ausschließlich in der folgenden Rolle. "
        "Keine Aufzählungen, ein oder zwei Sätze.\n\n"
        f"{role}"
    )
