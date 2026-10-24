# Privacy and third-party processing

Fernruf connects **your** SIP line to **your** chosen cloud AI services. Audio is processed on the host you control; some **text** is sent to third parties depending on configuration.

**This document is not legal advice.** Fernruf’s authors make **no compliance claim**. **You** (the deployer) are the **data controller** under the GDPR when you place calls that process callees’ personal data. Before production use you must:

- Identify a **legal basis** (e.g. consent, contract, legitimate interest where applicable) for processing callee data.
- **Inform callees** appropriately (transparency, purpose, recipients, retention, rights).
- Review each provider’s **terms**, **data processing agreement**, **sub-processors**, **data location**, and **retention**.
- Assess **recording** separately (e.g. §201 StGB in Germany requires consent before recording).

Providers you typically configure:

| Provider | Role |
|----------|------|
| **SIP / telephony** | Carries voice to the PSTN; may log numbers, timestamps, CLI. |
| **Fish Audio** | Text-to-speech for the agent’s spoken lines. |
| **OpenRouter** (or another OpenAI-compatible endpoint) | Task text and **conversation content** (including callee utterances as text) for the agent model; may forward to the **downstream model provider** (possibly outside the EU). |
| **TypeSafe Jev** (`api.typesafe.ai`) | **Optional.** Language detection from task text and/or **emotion inference** from callee transcript text. **Off by default** (`FERNRUF_TYPESAFE_ENABLED=false`, `FERNRUF_MOOD_ENABLED=false`). |

Local processing on your host (no Fernruf-initiated upload for this step):

| Step | Data | Where |
|------|------|--------|
| Speech-to-text | Callee **audio** | **Local** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your machine |
| Asterisk / RTP | Audio streams | Your LAN / container |
| Policy, allowlist, audit | Numbers, metadata | Your disk (`data/`) |

## Data-flow summary

| Data | Sent to | Why | Processing location (typical) |
|------|---------|-----|--------------------------------|
| Callee voice (audio) | — (local STT) | Transcription | **Your host** |
| Callee utterances (text) | OpenRouter → model provider | Agent replies, summaries | **Provider-dependent** (may be US or global) |
| Callee utterances (text) | TypeSafe Jev | **Optional** mood / emotion tag for TTS | **TypeSafe** (only if `FERNRUF_MOOD_ENABLED=true` and TypeSafe enabled) |
| Task / goal / context (text) | OpenRouter → model provider | Call behavior | **Provider-dependent** |
| Task text | TypeSafe Jev | **Optional** call language detection | **TypeSafe** (only if `FERNRUF_TYPESAFE_ENABLED=true`) |
| Agent reply text (before speak) | Fish Audio | Voice output | **Fish Audio** |
| Phone numbers, call IDs | SIP provider; your logs | Dialing, audit | **Your host** + **SIP provider** |

**Default public configuration:** no TypeSafe/Jev calls. Language is chosen via explicit `language`, local heuristics, or saved `default_language`. Set `FERNRUF_PRIVACY_MINIMAL=true` to force TypeSafe and mood off.

## AI transparency (EU AI Act Art. 50)

Fernruf is designed so callees hear **who** is calling and **that** the voice is AI-generated, before the conversational task proceeds.

- **Synthetic voice:** Spoken agent lines are produced by your configured **text-to-speech** integration (see the provider table above), not by a human speaker in real time.
- **Fixed opening notice:** Each call starts with a **fixed, language-specific spoken notice** that an **AI assistant** is calling **on behalf of the principal** you configure (`FERNRUF_PRINCIPAL_NAME`). The wording is part of the product’s phrase set for that call language.
- **Not disableable via MCP:** That disclosure is **always** used for the opening AI notice. Remote settings (including MCP) **cannot** turn it off or replace it with a custom `opening_line`; only CLI-side configuration and the built-in phrase logic apply.
- **Your responsibility:** EU and national rules on AI transparency (including **Article 50** of the EU AI Act where it applies) depend on **how and where you deploy** Fernruf. **You** must decide whether this notice meets your obligations and what additional information callees need. This section describes product behavior only; it is **not legal advice** and **not a compliance statement**.

## Emotion inference (mood)

When **`FERNRUF_MOOD_ENABLED=true`** and TypeSafe is active, **callee transcript text** is classified for emotion to optionally adjust Fish Audio tags. That is **inference about the callee** and may trigger **additional transparency obligations** under the **EU AI Act** (Art. 50(3) concerning emotion recognition) and **GDPR** fairness/transparency principles. Enable only with explicit deployer policy and callee-facing notice where required.

## Optional spoken privacy line

`FERNRUF_SPOKEN_PRIVACY_ENABLED=true` (default off) adds a **fixed per-language** privacy sentence after the AI disclosure. The text is not configurable via environment variables.

## Deployer checklist

1. Read [README.md](README.md#legal--abuse-notice) (abuse and telemarketing rules).
2. Sign DPAs / review terms: **SIP provider**, **Fish Audio**, **OpenRouter**, **downstream LLM vendor**, and **TypeSafe** if enabled.
3. Document your legal basis and callee information.
4. Keep `FERNRUF_TYPESAFE_ENABLED=false` unless you accept Jev processing.
5. Never log or paste API keys, SIP passwords, or full phone numbers in shared agent transcripts.

**No warranty:** Software is provided Apache-2.0; **you** are responsible for lawful use.
