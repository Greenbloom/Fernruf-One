# Fernruf architecture

Fernruf connects an AI agent to the public telephone network through **your own SIP line** (Asterisk as a LAN IP phone on your SIP registrar or home router).

## Components

| Layer | Role |
|--------|------|
| **MCP service** (`fernruf serve`) | OAuth or static token; tools to place calls, read transcripts, manage settings |
| **Call manager** | One call at a time; policy gate (allowlist, budgets, quiet hours) |
| **M2 voice pipeline** | Pipecat + Asterisk chan_websocket: STT (faster-whisper), LLM (OpenRouter-compatible), TTS (Fish Audio) |
| **Asterisk container** | Registers to `SIP_*`; ARI for originate; media WebSocket to the app |

Default MCP bind: `127.0.0.1:8787`.

## Data paths

- SQLite call settings and call records under `data/` (gitignored)
- Audit JSONL (`FERNRUF_AUDIT_PATH`)

## Safety (non-negotiable)

- Fixed AI disclosure at call start (`FERNRUF_PRINCIPAL_NAME`)
- Allowlist always enforced
- Marketing / cold-call tasks rejected
- Premium-rate prefixes blocked

See README legal notice and `tests/test_safety_defaults.py`.
