# Fernruf One

**AI phone calls for agents, via your own SIP line.**

Fernruf connects an MCP-capable agent to the public telephone network through **your** SIP trunk (Asterisk registered as a LAN IP phone on a home router or SIP registrar). The agent queues outbound calls, reads transcripts, and respects hard safety limits (allowlist-only dialing, fixed AI disclosure, no marketing tasks).

## Architecture

```
Agent (MCP) → Fernruf service → M2 voice pipeline → Asterisk → SIP → PSTN
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [docs/call-flow.md](docs/call-flow.md), and [docs/SERVER.md](docs/SERVER.md).

## Quick start

1. Install [Python 3.12+](https://www.python.org/downloads/) and [uv](https://docs.astral.sh/uv/), then run `uv sync` from the repo root (`uv sync --group dev` for tests).
2. Copy `.env.example` to `.env` and set at least:
   - `FERNRUF_PRINCIPAL_NAME` (required — spoken in the fixed AI disclosure; example: `Alex Example`, replace with your name)
   - `SIP_*`, `ASTERISK_ARI_PASSWORD`
   - `FERNRUF_ALLOWLIST` or use `fernruf settings allow-number +493023125123`
   - `FERNRUF_LLM_MODEL`, `OPENROUTER_API_KEY`, `FISH_AUDIO_API_KEY`, `FISH_VOICE_ID`
3. Create the Asterisk bind-mount directories, then start Asterisk:

   ```bash
   mkdir -p data/asterisk-sounds data/asterisk-generated data/asterisk-monitor
   docker compose up -d asterisk
   ```

   Those directories ship as empty placeholders. Create them before Compose when they are missing. Docker creates a missing `./data` as root, and Fernruf then cannot write `data/audit.jsonl`, `data/latency.jsonl`, generated sounds, or the demo MixMonitor wav.
4. CLI test (mock): `FERNRUF_LLM_MOCK=true FERNRUF_VOICE_MOCK=true uv run fernruf m2-test-call --simulate --to +493023125123 --task "Kurzer Test"`
5. MCP service: `uv run fernruf serve` (default `127.0.0.1:8787`; set `FERNRUF_MCP_TOKEN` or configure GitHub OAuth)

Setup details: [docs/SETUP.md](docs/SETUP.md). **AI agents (full host bootstrap):** [docs/AGENT-SETUP.md](docs/AGENT-SETUP.md). Testing: [docs/TESTING.md](docs/TESTING.md).

### MCP client (example)

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

Use GitHub OAuth instead of a static token when configured (`FERNRUF_GITHUB_*`, `FERNRUF_OAUTH_*`).

### Local call monitor (KDE)

`fernruf serve` also listens on `127.0.0.1:8791` (override the port with `FERNRUF_MONITOR_PORT`). That socket is loopback only. It does not follow `FERNRUF_BIND_OVERRIDE`. Set `FERNRUF_MONITOR_ENABLED=false` to leave it closed. Auth is `FERNRUF_MONITOR_TOKEN` when that is set, otherwise the same static `FERNRUF_MCP_TOKEN`. An OAuth-only service has no monitor until one of those tokens is set.

On the Plasma machine:

```bash
uv sync --extra desktop
uv run --extra desktop fernruf monitor
```

The process sits in the tray and raises a window when a call starts. The window is a placeholder. Replace `fernruf/desktop/MonitorPlaceholder.qml` when the mockup arrives. The Python object behind it keeps the names `number`, `state`, `elapsed`, `listening`, `volume`, `muted`, and `summaryLine`. The transcript list is `lineModel`.

Listening is off until you turn it on. The checkbox is stored in `~/.config/fernruf/listen.json` (`listen`, default false). Volume and mute affect only this computer's playback. The window cannot speak into the call or hang it up. Nothing from this socket leaves the machine.

Text frames are JSON with `"v": 1`.

- Client, if the handshake has no `Authorization: Bearer` header: `{"type":"auth","token":"..."}`. Server replies `{"type":"auth_ok"}`.
- Client: `{"type":"listen","enabled":true}`. Audio is not sent until this is true. The server replies with the same shape.
- Server lifecycle: `{"type":"lifecycle","call_id","state","at","to_masked","outcome","summary"}`. `state` is `dialing`, `ringing`, `answered`, `ended`, then `finalized`. `to_masked` keeps the last four digits. `summary` is present only on `finalized`.
- Server transcript: `{"type":"transcript","call_id","speaker","text","t_ms","final"}`. `speaker` is `agent` or `callee`. Current calls send `final: true`.

Binary frames are little-endian `FR1`. The direction byte is 1 for the callee and 2 for the bot's speech. Then the frame carries the sample rate, the channel count, a timestamp, the call id, and s16le PCM. A subscriber that falls behind loses frames. The call does not wait.

Optional CUDA STT acceleration: `uv sync --extra cuda` (proprietary GPU vendor wheels).

## Legal & abuse notice

**You are responsible for every call Fernruf places on your line.**

- **Germany — §201 StGB:** Recording a call requires prior consent. Fernruf does **not** record calls in this version; it may produce local transcripts/summaries and send text to configured providers as described in [PRIVACY.md](PRIVACY.md). The optional consent question in prompts is a hint only until recording exists. The local monitor can play a live call on this computer. That switch defaults to off. It does not save audio. It does not change the disclosure or the consent question.
- **Germany — UWG §7(2):** Unsolicited advertising calls to consumers are prohibited; B2B calls require a legitimate interest or consent. Fernruf rejects marketing-style tasks and is **allowlist-only**.
- **Transparency:** Every call begins with a mandatory fixed AI disclosure naming `FERNRUF_PRINCIPAL_NAME`. The model cannot skip or rewrite it. Optional `FERNRUF_SPOKEN_PRIVACY_ENABLED=true` adds a fixed per-language processing notice after the disclosure (off by default). See [PRIVACY.md](PRIVACY.md).
- **Caller ID:** Follow BundesnetzAgentur rules; do not spoof numbers you do not legitimately use.
- **No robocold-calling:** Configure only numbers you own or that have **prior express consent**. The operator is liable for misuse.

Fernruf is a tool, not legal advice. Read your SIP provider, Fish Audio, and OpenRouter terms before production use. **Data flows and GDPR responsibilities:** [PRIVACY.md](PRIVACY.md).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Trademarks: third-party names (Fish Audio, OpenRouter, TypeSafe, Asterisk, Pipecat, and others) belong to their owners. Fernruf is not affiliated with or endorsed by them.
