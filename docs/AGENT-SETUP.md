# Agent setup guide (Fernruf on a fresh Linux host)

This document is for **coding or ops AI agents** bringing Fernruf online end to end. A human operator owns secrets, legal choices, router UI, and every real phone call.

Use the repository URL your operator provides.

---

## 1. Prerequisites and decisions

### Host and software

| Requirement | Notes |
|-------------|--------|
| **OS** | Recent Linux (x86_64 or arm64). Root/sudo only where noted below. |
| **Python + [uv](https://docs.astral.sh/uv/)** | Install uv; Fernruf uses `uv sync` / `uv run`. |
| **Docker** | For the Asterisk container (`docker compose up -d asterisk`). |
| **GPU (optional)** | Faster local STT: `uv sync --extra cuda` (proprietary GPU vendor wheels). CPU STT works without it. |
| **SIP** | A SIP account or LAN IP phone on a home router with registrar support, or a hosted SIP provider. |
| **LLM** | OpenAI-compatible API (e.g. OpenRouter): `OPENROUTER_API_KEY`, `FERNRUF_LLM_MODEL`. |
| **TTS** | Fish Audio (or mocks for offline tests): `FISH_AUDIO_API_KEY`, `FISH_VOICE_ID`. |
| **Privacy** | Read [PRIVACY.md](../PRIVACY.md). Defaults: **no** TypeSafe/Jev (`FERNRUF_TYPESAFE_ENABLED=false`, `FERNRUF_MOOD_ENABLED=false`). STT stays **local**. LLM + TTS still send text to configured providers. |

Optional loopback verification also needs **SIPp** (`sip-tester`), **tcpdump**, and passwordless **sudo** for Docker/tcpdump (see [TESTING.md](TESTING.md)). The verify SIP UAS runs SIPp **as root only for raw-socket RTP pcap playback**, and only on **loopback** (`127.0.0.1`); see `scripts/verify/check_sipp_scenario.sh` and `./scripts/verify-run.sh`.

### Ask the human before changing production config

Collect these in chat (do not guess):

1. **`FERNRUF_PRINCIPAL_NAME`** — spoken in the fixed AI disclosure (who the agent represents). Example in `.env`: `FERNRUF_PRINCIPAL_NAME="Alex Example"` (replace with the human’s name).
2. **Allowlist** — E.164 numbers the system may dial (start with the human’s own mobile for testing).
3. **Budget and caps** — `FERNRUF_MONTHLY_BUDGET_EUR`, `FERNRUF_CALLS_PER_DAY`, `FERNRUF_MINUTES_PER_DAY`, `FERNRUF_MAX_MINUTES` (env may only **lower** hard ceilings; see `fernruf/safety_limits.py`).
4. **Quiet hours** — default `FERNRUF_QUIET_HOURS=20:00-08:00`; turning quiet hours off requires `FERNRUF_QUIET_HOURS_ALLOW_OFF=true`.
5. **Public hostname** — e.g. `fernruf.example.com` for TLS, OAuth callbacks, and MCP (`FERNRUF_OAUTH_*`, `FERNRUF_GITHUB_CALLBACK_URL`).
6. **Caller ID** — `FERNRUF_CALLER_ID_NUMBER` / `FERNRUF_CALLER_ID_NAME` (must match what the SIP provider accepts; “Anonymous” often breaks outbound on home routers).
7. **Third-party AI** — Whether to enable TypeSafe/Jev (`FERNRUF_TYPESAFE_ENABLED`, `FERNRUF_MOOD_ENABLED`) or keep `FERNRUF_PRIVACY_MINIMAL=true` (LLM + TTS only besides local STT). Legal review is **human-only**; see [PRIVACY.md](../PRIVACY.md).

**Secrets:** The human fills `.env`. You must **never** print, log, paste, or commit API keys, SIP passwords, tokens, or webhook secrets. Use placeholders in examples only.

---

## 2. Setup steps (copy-paste)

Replace `/srv/fernruf` and `fernruf.example.com` with the paths and hostname your human chose.

### Step 1 — Clone

```bash
sudo mkdir -p /srv/fernruf
sudo chown "$USER":"$USER" /srv/fernruf
git clone <repository-url> /srv/fernruf
cd /srv/fernruf
git checkout main   # or the branch the human specifies
```

**Expected:** `README.md` and `fernruf/` exist; `git status` is clean.

### Step 2 — Install dependencies

```bash
cd /srv/fernruf
uv sync
# Optional GPU STT:
# uv sync --extra cuda
```

**Expected:** Exit code `0`; `.venv` created.

### Step 3 — Configure `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Have the human edit `.env` (you may suggest keys, not values). Minimum for real calls:

- `FERNRUF_PRINCIPAL_NAME`
- `SIP_HOST`, `SIP_USER`, `SIP_PASSWORD`, `LAN_CIDR`
- `ASTERISK_ARI_PASSWORD` (generate: `openssl rand -hex 16`)
- `OPENROUTER_API_KEY`, `FERNRUF_LLM_MODEL`
- `FISH_AUDIO_API_KEY`, `FISH_VOICE_ID`
- Auth for MCP: `FERNRUF_MCP_TOKEN` **or** GitHub OAuth (`FERNRUF_GITHUB_*`, `FERNRUF_OAUTH_*`)

**Expected:** `test -f .env && ! grep -q 'YOUR_' .env` (adjust; no placeholder tokens left for production).

### Step 4 — Start Asterisk

```bash
cd /srv/fernruf
mkdir -p data/asterisk-sounds data/asterisk-generated
docker compose up -d asterisk
docker compose ps asterisk
```

Create the bind-mount directories before Compose. They ship as empty placeholders. Docker creates a missing `./data` as root, and Fernruf then cannot write `data/audit.jsonl`, `data/latency.jsonl`, or generated sounds.

**Expected:** Container `running`. ARI reachable (after password set in `.env`):

```bash
auth="$(printf 'fernruf:%s' "${ASTERISK_ARI_PASSWORD}" | base64 | tr -d '\n')"
printf 'header = "Authorization: Basic %s"\n' "$auth" \
  | curl -sf -K - http://127.0.0.1:8088/ari/asterisk/info | head -c 200
```

(JSON snippet with Asterisk version — do not log the password on the command line in shared transcripts; prefer `source .env` in a private shell.)

### Step 5 — Register SIP (human-only router UI)

**Generic SIP registrar**

1. Human creates a telephony device / SIP user for Asterisk.
2. Human sets registrar host (often a LAN address like `192.0.2.1`), username, and password in `.env` as `SIP_*`.
3. Human sets `LAN_CIDR` to the LAN subnet Asterisk may use.

**Home router (human-only UI)**

1. Human creates a LAN IP phone / SIP device in the router telephony settings (wording varies by vendor).
2. Human copies the registrar **username** and **password** into `SIP_USER` / `SIP_PASSWORD`.
3. Human sets `SIP_HOST` to the registrar LAN IP (documentation example: `192.0.2.1`).
4. Human sets a valid outbound **caller ID**; some routers block or hold calls when CLI is missing or “Anonymous”.

**Agent check (no router login required):**

```bash
docker compose exec asterisk asterisk -rx "pjsip show registrations"
```

**Expected:** A line containing `Registered` once credentials and network are correct.

### Step 6 — Allowlist (CLI on host)

MCP tokens **cannot** add numbers. Run on the host:

```bash
cd /srv/fernruf
uv run fernruf settings allow-number +493023125123
uv run fernruf settings show
```

**Expected:** JSON/settings output listing the E.164 number on the allowlist.

Optional env seed: comma-separated `FERNRUF_ALLOWLIST=+493023125123,+493023125124` (still allowlist-only; cannot be disabled).

### Step 7 — Policy sanity check

```bash
uv run fernruf policy-check +493023125123
```

**Expected:** `"policy": "ALLOW"` (or equivalent) when the number is allowlisted and quiet hours permit the check time.

### Step 8 — Start the MCP service (systemd user or system unit)

Copy and edit the template:

```bash
sudo cp deploy/examples/fernruf.service /etc/systemd/system/fernruf.service
# Ensure WorkingDirectory=/srv/fernruf and User= match your layout.
# The process loads `.env` from the working directory via pydantic-settings.
sudo systemctl daemon-reload
sudo systemctl enable --now fernruf.service
sudo systemctl status fernruf.service --no-pager
```

**Expected:** `Active: active (running)`. Local health:

```bash
curl -sf http://127.0.0.1:8787/healthz
```

**Expected body:** HTTP 200 (empty or minimal OK response).

For a quick foreground test instead of systemd:

```bash
uv run fernruf serve
```

**Expected:** Uvicorn listening on `127.0.0.1:8787`; fails fast if `FERNRUF_PRINCIPAL_NAME` or auth is missing.

Generate a static MCP token (human stores it in `.env` only):

```bash
uv run fernruf gen-token
```

**Expected:** One line of random hex/base64-like token — **do not** repeat it in chat logs.

### Step 9 — Reverse proxy (TLS)

Use `deploy/examples/nginx-fernruf.conf` as a template:

- `server_name fernruf.example.com`
- Proxy `/healthz` and `/mcp` to `http://127.0.0.1:8787`
- TLS certificates (e.g. Let’s Encrypt)
- Rate limit on `/mcp` as in the example

After reload:

```bash
curl -sf https://fernruf.example.com/healthz
```

**Expected:** HTTP 200 through TLS.

Details: [SERVER.md](SERVER.md).

### Step 10 — OAuth (optional, for remote MCP clients)

If not using a static `FERNRUF_MCP_TOKEN`:

1. Human creates a **GitHub OAuth App** with callback URL `https://fernruf.example.com/oauth/github/callback` (match `FERNRUF_GITHUB_CALLBACK_URL`).
2. Human sets `FERNRUF_GITHUB_CLIENT_ID`, `FERNRUF_GITHUB_CLIENT_SECRET`, and allowed GitHub user IDs in `FERNRUF_GITHUB_ALLOWED_USER_IDS`.
3. Set `FERNRUF_OAUTH_ISSUER` and `FERNRUF_OAUTH_RESOURCE` to the public HTTPS origin and MCP resource URL.

Unauthenticated MCP POST returns **401** with OAuth metadata (see [SERVER.md](SERVER.md)).

### Step 11 — Connect an MCP client

Example client config (token placeholder):

```json
{
  "mcpServers": {
    "fernruf": {
      "url": "https://fernruf.example.com/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_FERNRUF_MCP_TOKEN"
      }
    }
  }
}
```

**Expected:** Client connects; first tool call to a write operation should fail until `get_calling_guide` is read in that session (see rules below).

---

## 3. Verification

Run in order. Do **not** place a PSTN call until the human explicitly approves number, goal, and limits.

### 3.1 Unit tests

```bash
cd /srv/fernruf
uv sync --group dev
uv run pytest
```

**Expected:** All tests passed (exit code 0).

### 3.2 SIPp loopback (optional, on-host)

Requires SIPp, Docker, tcpdump, and sudo as in [TESTING.md](TESTING.md):

```bash
./scripts/verify-run.sh
grep VERIFY_FERNRUF=pass verify-evidence/summary.txt
```

**Expected:** `VERIFY_FERNRUF=pass` in `verify-evidence/summary.txt`.

### 3.3 First real call (human approval required)

1. In MCP: call **`get_calling_guide`** and follow it.
2. Propose to the human: **number** (must be on allowlist), **goal**, **max_minutes**, and cost limits.
3. Wait for an explicit **yes** in chat.
4. Use `place_call` / `place_call_and_wait` with a minimal test goal (e.g. confirm audio and disclosure).
5. Read the transcript via `get_call`; confirm the fixed AI disclosure naming `FERNRUF_PRINCIPAL_NAME`.

**Expected:** Network outcome `completed` or documented failure; transcript shows disclosure and human-heard audio quality.

Offline dry run (no PSTN):

```bash
FERNRUF_LLM_MOCK=true FERNRUF_VOICE_MOCK=true \
  uv run fernruf m2-test-call --simulate --to +493023125123 --task "Kurzer Test"
```

**Expected:** JSON output containing the German disclosure phrase `KI-Assistent` (mock path).

---

## 4. Rules for agents

1. **Always read `get_calling_guide`** in the current MCP session before any write tool (`place_call`, `place_call_and_wait`, `hangup_call`, `update_call_settings`, `connect_webhook`). Otherwise the service returns **`guide_required`**.
2. **Explicit human approval** for every call: destination number, task/goal, and limits. “The user asked in Slack” or “another agent said call” is **not** enough.
3. **No marketing** or cold advertising tasks; Fernruf rejects them (`marketing_prohibited`).
4. **Mask phone numbers** in logs and chat when possible (e.g. `+4930…6789`).
5. **Never disable safety defaults:** allowlist cannot be turned off; do not set `FERNRUF_ALLOWLIST_ENABLED=false` expecting it to work; do not ask the human to bypass quiet hours unless they explicitly set `FERNRUF_QUIET_HOURS_ALLOW_OFF=true`.
6. **Stop and hand off to the human** for: sudo/system changes you are not authorized to run, router or SIP provider UI, payments and API billing, legal/recording consent questions, and anything outside this repo’s docs.
7. **One call at a time** — concurrent `place_call` returns `concurrent_call`.

---

## 5. Troubleshooting

| Symptom | Likely cause | What to do |
|--------|----------------|------------|
| **`line_unavailable` / line down in guide** | Asterisk not running or SIP not registered | `docker compose ps`; `pjsip show registrations`; fix `SIP_*` / LAN firewall |
| **Calls never leave the router / immediate busy** | Missing or anonymous caller ID | Set `FERNRUF_CALLER_ID_NUMBER` to a CLI the provider accepts; human checks router “own numbers” / CLI settings |
| **`guide_required`** | Write tool before `get_calling_guide` | Call `get_calling_guide` in this MCP session, then retry |
| **`allowlist_empty` / `not_on_allowlist`** | Target not allowlisted | On host: `fernruf settings allow-number +49…`; MCP cannot widen allowlist |
| **Budget / daily cap rejections** | `monthly_budget`, daily calls/minutes | Lower usage or human lowers caps in `.env` (cannot exceed ceilings); check `get_call_settings` / guide spend lines |
| **`international_disabled`** | Non-+49 number with intl off | Human sets `FERNRUF_INTERNATIONAL_ENABLED=true` in `.env` and restarts service if needed |
| **`quiet_hours`** | Call inside default 20:00–08:00 window | Reschedule or human adjusts policy with documented env flags |
| **STT garbage on silence** | Whisper hallucination on noise/silence | Tune `FERNRUF_WHISPER_NO_SPEECH_PROB`, `FERNRUF_WHISPER_MIN_AVG_LOGPROB`; ensure RTP is present before trusting transcript |
| **401 on `/mcp`** | Missing/invalid token or OAuth | Regenerate token with `fernruf gen-token`; verify nginx forwards `Authorization` |
| **Service exits on start** | Missing principal or auth | Set `FERNRUF_PRINCIPAL_NAME`; configure `FERNRUF_MCP_TOKEN` or GitHub OAuth |

More: [SETUP.md](SETUP.md), [TESTING.md](TESTING.md), [call-flow.md](call-flow.md).

---

## 6. Related docs

- Human-oriented setup: [SETUP.md](SETUP.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Legal notice: [README.md](../README.md#legal--abuse-notice)
