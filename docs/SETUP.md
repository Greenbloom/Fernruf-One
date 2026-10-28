# Setup (SIP + Asterisk)

## 1. SIP account / LAN IP phone

On a **home router with IP-phone / SIP registrar support** (or any SIP provider):

1. Create a **LAN IP phone** / telephony device for Asterisk.
2. Note registrar host (often a LAN address like `192.0.2.1`), **SIP user**, and password.
3. Set `SIP_HOST`, `SIP_USER`, `SIP_PASSWORD`, `LAN_CIDR` in `.env`.
4. Outbound-only: do not route inbound PSTN to Asterisk unless you intend to.

## 2. Asterisk container

```bash
mkdir -p data/asterisk-sounds data/asterisk-generated data/asterisk-monitor
docker compose up -d asterisk
```

Create those directories before Compose. They are empty placeholders in the repo (`data/.gitkeep` and the subdirectories). Docker creates a missing `./data` as root when it bind-mounts `./data/asterisk-sounds`, `./data/asterisk-generated`, and `./data/asterisk-monitor`, and Fernruf then cannot write `data/audit.jsonl`, `data/latency.jsonl`, or generated sounds. If a write still fails with permission denied, the audit and latency logs name this `mkdir` command.

Generate `ASTERISK_ARI_PASSWORD`: `openssl rand -hex 16`

## 3. Fernruf configuration

Required:

- `FERNRUF_PRINCIPAL_NAME`
- Allowlist numbers (`fernruf settings allow-number …`)
- LLM + Fish keys (or `FERNRUF_LLM_MOCK` / `FERNRUF_VOICE_MOCK` for offline tests)

## 4. Verify

See [TESTING.md](TESTING.md) and `scripts/verify-run.sh`.
