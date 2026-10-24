"""Calling guide shown to MCP clients before any write tool."""

from __future__ import annotations

from fernruf.service.policy_gate import PolicyLimits
from fernruf.service.records import spend_snapshot
from fernruf.settings import Settings


def render_calling_guide(
    settings: Settings,
    limits: PolicyLimits,
    *,
    line_ready: bool,
    spent_eur: float,
    now_label: str,
) -> str:
    principal = settings.require_principal()
    remaining = max(0.0, limits.monthly_budget_eur - spent_eur)
    line = "up" if line_ready else "down"
    return f"""# Fernruf calling guide

This service places a real outbound phone call through your SIP line. You are mission control. You do not speak on the call.

## Before every write

1. Read this guide in the current MCP session. `place_call`, `place_call_and_wait`, `hangup_call`, `update_call_settings`, and `connect_webhook` fail with `guide_required` until you do.
2. Show the operator the number, the goal, and the limits in chat and get an explicit yes. A network `completed` outcome does not mean the task succeeded. Check the transcript.
3. One call at a time. A second `place_call` returns `concurrent_call`.

## The brief (`task`)

- `goal` (required): what the call must achieve.
- `context`: facts the agent may use.
- `questions`: list of questions that must be answered.
- `must_say`: facts that have to be spoken.
- `must_not`: things the agent must not agree to or say.
- `decision_limits`: what the agent may agree to.
- `fallback`: what to do on voicemail or a phone menu.
- Example JSON:

```json
{{
  "goal": "Ask whether Tuesday at 15:00 still works for the handover.",
  "context": "The operator asked to confirm the appointment.",
  "questions": ["Does Tuesday at 15:00 work?"],
  "must_say": ["The documents are ready in the shared folder."],
  "must_not": ["Do not agree to extra fees."],
  "decision_limits": "Confirm time only; no new commitments.",
  "fallback": "If voicemail, leave one sentence that the operator will call back."
}}
```

- `persona` (optional, separate argument): how the agent should sound.
- `max_minutes`: at most {limits.max_minutes_ceiling} (the env ceiling; hard cap 30).
- `model` (optional): OpenRouter or other OpenAI-compatible model id for this call only. Omit it to use the saved setting, then `FERNRUF_LLM_MODEL`.
- `record`: default false. Recording is not stored in this version; `get_recording` returns not available until consent and storage exist.

## What the callee hears

Every call starts with a fixed AI disclosure the model cannot change. In German that is "Hallo, hier ist ein KI-Assistent, der im Auftrag von {principal} anruft." The same fact is spoken in the call language. Pass `language` (for example `en`) to force that language; it overrides detection. If you omit `language`, Fernruf uses local heuristics and `default_language` for German, English, and Chinese only; other languages need an explicit `language` unless the host enables TypeSafe Jev (`FERNRUF_TYPESAFE_ENABLED=true`). See PRIVACY.md on third-party processing.

## Policy right now ({now_label})

- Allowlist is always on. Numbers on it: {limits.allowlist and len(limits.allowlist) or 0}. An empty allowlist blocks every number (`allowlist_empty`). The MCP token cannot add numbers. On the host: `fernruf settings allow-number +49…`.
- Default country is +49. International calls are {"enabled" if limits.international_enabled else "disabled"} (`international_disabled`). That switch is CLI-only on the host.
- Blocked prefixes: 0900, 0137, 0180, 0700, 118xx, and star-code internals such as `**99` (`blocked_prefix`, `internal_star_dial`).
- No marketing or cold advertising calls (`marketing_prohibited`).
- Quiet hours: {limits.quiet_hours.display()}.
- Per call: at most {limits.max_minutes_ceiling} minutes. Per day: {limits.calls_per_day:g} calls and {limits.minutes_per_day:g} minutes. Per month: {limits.monthly_budget_eur:.2f} EUR (mobile telephony blocked at {limits.mobile_budget_eur:.2f} EUR when mobile flat is off). The MCP cannot raise these above the env ceilings.
- Remaining budget: {remaining:.2f} EUR. Spent this month: {spent_eur:.2f} EUR.
- Line (Asterisk registered to SIP): {line}. If it is down, `place_call` returns `line_unavailable`.
- Mobile flat rate: {"on" if limits.mobile_flat_enabled else "off"}.

## After the call

`get_call` returns status, timings, outcome, transcript, and cost. `summary` stays null until the structured summary lands in a later version. `outcome` is only the network result. `get_recording` is `not_available` when nothing was recorded. `connect_webhook` stores a URL; the signed `call.finalized` event is not sent yet.
"""


def guide_for_settings(
    settings: Settings,
    limits: PolicyLimits,
    *,
    line_ready: bool,
    now,
) -> str:
    spent = spend_snapshot(settings, now=now)
    label = now.astimezone(limits.quiet_hours.timezone).strftime("%Y-%m-%d %H:%M %Z")
    return render_calling_guide(
        settings,
        limits,
        line_ready=line_ready,
        spent_eur=float(spent["spent_eur"]),
        now_label=label,
    )
