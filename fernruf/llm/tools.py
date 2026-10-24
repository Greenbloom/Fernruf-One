from __future__ import annotations

CALL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "send_dtmf",
            "description": "Send DTMF digits on the active phone call (RFC 4733). Use w for pause.",
            "parameters": {
                "type": "object",
                "properties": {
                    "digits": {
                        "type": "string",
                        "description": "Digits 0-9, *, #, and w for 500ms pause",
                    }
                },
                "required": ["digits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": (
                "End the phone call only after the callee has answered, refused, or "
                "said they do not know. goodbye is the one short farewell in the "
                "call language, spoken once before hangup. If the callee gave a "
                "concrete answer to the task (a time, date, yes/no, number, or "
                "name), confirm that answer in one short clause and attribute any "
                "follow-up to the principal, not to yourself. English example: 'Thank you, "
                "then the principal will get in touch at 7 p.m. Goodbye.' Say the same "
                "thing in the call language, ending with that language's goodbye. "
                "Never promise an action you cannot do (calling back, sending "
                "something) unless the task says so. Do not replace that fact "
                "with a general well-wish. Otherwise a short thanks and the call "
                "language's goodbye. Never call this in the same turn as a "
                "question or request, including indirect questions without a question "
                "mark. Not for early test hangups. Do not call this before every "
                "must_say line has been spoken, unless the callee clearly hangs "
                "up or refuses. 'Vielen Dank' or 'thanks' alone is neither."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Short reason for hangup (logged, not spoken verbatim).",
                    }
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_answer",
            "description": "Record an answer to one of the brief's questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question", "answer"],
            },
        },
    },
]
