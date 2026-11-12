# Fernruf One

[![CI](https://github.com/Greenbloom/Fernruf-One/actions/workflows/ci.yml/badge.svg)](https://github.com/Greenbloom/Fernruf-One/actions/workflows/ci.yml)

Fernruf One places phone calls for an MCP agent on a SIP line you control. The agent queues a call, reads the transcript, and hangs up. Asterisk registers as an IP phone and carries the audio.

## Architecture

```
Agent (MCP) → Fernruf service → voice pipeline → Asterisk → SIP → PSTN
```

Speech-to-text runs on the host with faster-whisper. The language model is any OpenAI-compatible endpoint. Fish Audio speaks the agent's lines. [ARCHITECTURE.md](ARCHITECTURE.md) lists the components. [docs/call-flow.md](docs/call-flow.md) is the call state diagram.

## Quick start

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
```

Set `FERNRUF_PRINCIPAL_NAME`, the SIP registrar values, `ASTERISK_ARI_PASSWORD`, and one allowlisted number. The name in `.env.example` is a placeholder.

```bash
mkdir -p data/asterisk-sounds data/asterisk-generated data/asterisk-monitor
docker compose up -d asterisk
FERNRUF_LLM_MOCK=true FERNRUF_VOICE_MOCK=true uv run fernruf m2-test-call --simulate --to +493023125123 --task "Kurzer Test"
uv run fernruf serve
```

Create those three directories before Compose. If Docker creates `./data` as root, Fernruf cannot write its logs or generated sounds.

`fernruf serve` listens on `127.0.0.1:8787`. Set `FERNRUF_MCP_TOKEN`, or the GitHub OAuth variables, before you expose it. The rest of host setup is in [docs/SETUP.md](docs/SETUP.md).

An MCP client points at the service like this.

```json
{
  "mcpServers": {
    "fernruf": {
      "url": "https://fernruf.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_FERNRUF_MCP_TOKEN"
      }
    }
  }
}
```

Use GitHub OAuth instead of a static token when `FERNRUF_GITHUB_CLIENT_ID` and `FERNRUF_GITHUB_CLIENT_SECRET` are set.

Optional CUDA speech-to-text wheels are `uv sync --extra cuda`. Those wheels follow the GPU vendor license.

## Configuration

Copy `.env.example` to `.env`. Fernruf reads `.env` from the working directory. Set `FERNRUF_ENV_FILE` to `none` to skip that file. Do not commit `.env`.

| Variable | Default | Role |
| --- | --- | --- |
| `FERNRUF_PRINCIPAL_NAME` | empty | Name spoken in the fixed AI disclosure. Required before a real call. |
| `SIP_HOST`, `SIP_USER`, `SIP_PASSWORD` | `192.0.2.1`, empty, empty | Registrar host, username, and password. |
| `LAN_CIDR` | `192.0.2.0/24` | Documentation range in the example. Set the LAN that may reach SIP. |
| `ASTERISK_ARI_URL` | `http://127.0.0.1:8088` | Asterisk ARI. |
| `ASTERISK_ARI_PASSWORD` | empty | Required. A placeholder value is refused. |
| `FERNRUF_ALLOWLIST` | empty | Comma-separated E.164 numbers. Empty blocks every call. |
| `FERNRUF_QUIET_HOURS` | `20:00-08:00` | Local quiet hours. |
| `FERNRUF_CALLS_PER_DAY` | 10 | Daily call cap. The ceiling is 50. |
| `FERNRUF_MINUTES_PER_DAY` | 100 | Daily minute cap. The ceiling is 300. |
| `FERNRUF_MONTHLY_BUDGET_EUR` | 25 | Monthly budget in EUR. The ceiling is 100. |
| `FERNRUF_MCP_TOKEN` | empty | Static bearer token for MCP. |
| `FERNRUF_BIND_HOST` | `127.0.0.1` | Address the MCP service binds. |
| `FERNRUF_BIND_PORT` | 8787 | MCP port. |
| `FERNRUF_LLM_MODEL` | empty | Model id for the OpenAI-compatible endpoint. |
| `OPENROUTER_API_KEY` | empty | Key for that endpoint. |
| `FISH_AUDIO_API_KEY`, `FISH_VOICE_ID` | empty | Speech synthesis. |

Whisper, OAuth, mood, and the monitor port are in `.env.example`.

## MCP tools

`get_calling_guide` has to succeed before a write tool in the same MCP session. Protocol 2026-07-28 opens a new connection per request and has no session id. For that protocol the gate follows a short hash of the bearer token for two hours.

| Tool | What it does |
| --- | --- |
| `get_calling_guide` | Limits, line state, and the rules for a call. Unlocks the write tools. |
| `get_balance` | Monthly budget, spend, and how many calls were placed today. |
| `get_call_settings` | Voice, limits, allowlist, and quiet hours. |
| `update_call_settings` | Changes settings that do not widen who can be called. |
| `place_call` | Queues one call and returns. |
| `place_call_and_wait` | Places a call and waits until it ends. |
| `get_call` | Status, transcript, cost, and the post-call summary. |
| `get_call_transcript` | Transcript lines for one call. |
| `list_calls` | Recent calls. |
| `get_recording` | Recording link. Returns `not_available` in this version. |
| `hangup_call` | Hangs up a queued or live call. |
| `connect_webhook` | Stores a webhook URL and sends a test event. |

`update_call_settings`, `place_call`, `place_call_and_wait`, `hangup_call`, and `connect_webhook` are the write tools.

## Safety

**AI notice.** Every call starts with a fixed sentence. It says that an AI assistant is calling for `FERNRUF_PRINCIPAL_NAME`. The model cannot skip that sentence or rewrite it.

**Consent before recording.** The system prompt keeps recording off until the callee agrees. If the agent asks, it has to use the fixed consent question for that language. `record` defaults to false. `get_recording` returns `not_available`. This version does not store a call recording for MCP clients. `fernruf demo-call` can write a wav on the host for its own playback. That file is not served by `get_recording`.

**Transcripts.** Fernruf can transcribe a call while it runs, show that text in the local call window, and store the transcript and a summary. The transcript is off by default, so a new install keeps the summary and drops the words at the end of the call. To turn it on, set `FERNRUF_TRANSCRIPT_ALLOWED=true` in `.env` before the settings database is created, or run `fernruf settings transcript on`. Showing, storing, or using what the other person said may require their consent. That depends on the jurisdiction.

**Allowlist.** Fernruf dials only numbers on the allowlist. The MCP token cannot add a number. Add one on the host with `fernruf settings allow-number`.

**Cost caps.** A deployment can lower the daily call cap, the daily minute cap, and the monthly euro budget. It cannot raise them above 50 calls, 300 minutes, or 100 EUR.

**Quiet hours.** The default window is 20:00 to 08:00. Turning quiet hours off requires `FERNRUF_QUIET_HOURS_ALLOW_OFF`.

Marketing-style tasks are rejected. Premium-rate prefixes are blocked. You are responsible for every call placed on your line. In Germany, the DSGVO covers stored text of what someone said, audio recording under §201 StGB needs prior consent, and unsolicited advertising calls are restricted under UWG §7. Fernruf is not legal advice. Data flows are in [PRIVACY.md](PRIVACY.md).

## Local call monitor

The same `fernruf serve` process can open a loopback socket for a tray window on that computer. The window, the auth token, and the frame format are in [docs/monitor.md](docs/monitor.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Third-party names belong to their owners. Fernruf is not affiliated with them.
