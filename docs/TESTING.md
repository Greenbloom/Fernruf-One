# Testing

```bash
uv sync --group dev
uv run pytest
```

## Loopback verify (optional)

With Docker and SIPp installed on the host:

```bash
./scripts/verify-run.sh
grep VERIFY_FERNRUF=pass verify-evidence/summary.txt
```

The verify SIP registrar UAS runs **SIPp as root** only so `play_pcap_audio` can use a raw socket for RTP replay. That applies **on loopback** (`127.0.0.1` by default via `VERIFY_SIP_HOST`); it is not meant for binding on public interfaces.

CI and local smoke without Docker:

```bash
./scripts/verify/run_policy_pchecks.sh
./scripts/verify/check_sipp_scenario.sh   # SIPp loopback UAS + UAC (sudo on loopback for pcap)
```

## Policy checks

```bash
uv run fernruf policy-check +493023125123
```

Allowlist must include the target number or the check returns `REJECT`.
