# Fernruf architecture

Fernruf connects an AI agent to the public telephone network through a SIP line you control. Asterisk registers as a LAN IP phone on your SIP registrar.

## Components

| Layer | Role |
| --- | --- |
| MCP service, `fernruf serve` | OAuth or a static token. Tools place calls, read transcripts, and manage settings. |
| Call manager | One call at a time. The policy gate checks the allowlist, budgets, and quiet hours. |
| Voice pipeline | Pipecat and Asterisk chan_websocket. Speech-to-text is local faster-whisper. The model is OpenAI-compatible. Speech is Fish Audio. |
| Asterisk container | Registers with the `SIP_*` settings. ARI originates the call. Media uses a WebSocket to the app. |

The MCP service binds `127.0.0.1:8787` by default.

## Data paths

- SQLite call settings and call records under `data/`. That directory is gitignored except for empty placeholders.
- Audit JSONL at `FERNRUF_AUDIT_PATH`.

## Safety

- Fixed AI disclosure at the start of a call, using `FERNRUF_PRINCIPAL_NAME`.
- Allowlist always enforced.
- Marketing and cold-call tasks rejected.
- Premium-rate prefixes blocked.

The legal notice is in [README.md](README.md). The ceilings are covered by `tests/test_safety_defaults.py`.
