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

## Local AI-to-AI demo

`fernruf demo-call` places a normal Fernruf call on a local Asterisk extension. It does not use the SIP trunk, a phone number, or the allowlist. The command refuses to start if the dial string would touch the trunk.

The callee is a second Pipecat bot (same STT and LLM stack, its own prompt and Fish voice). The default persona is a German receptionist at the fictional Praxis Beispiel, checking whether Tuesday works for an appointment. `--persona` replaces that prompt. `FERNRUF_DEMO_CALLEE_VOICE_ID` selects the callee voice and falls back to `FISH_VOICE_ID` when unset.

The Fernruf side is the normal pipeline: the fixed AI notice first (using `FERNRUF_PRINCIPAL_NAME`), the identity guard, `end_call` and the goodbye, then a summary.

Recreate Asterisk once so it loads the `fernruf-demo` context, the `fernruf-callee` websocket client, and the monitor volume:

```bash
mkdir -p data/asterisk-sounds data/asterisk-generated data/asterisk-monitor
docker compose up -d --force-recreate asterisk
```

The callee listens on `ws://127.0.0.1:8766/media` (`FERNRUF_DEMO_CALLEE_WS_PORT`). Asterisk uses `FERNRUF_DEMO_CALLEE_WS_URI` (the same default). Restart Asterisk after changing either one. Run the command from the repo root so it can read `data/asterisk-monitor`.

```bash
uv run fernruf demo-call \
  --task "Frag, ob Dienstag für einen Termin passt." \
  --out data/demo/launch \
  --video
```

`--video` needs ffmpeg with libx264, aac, the subtitles filter, and drawtext.

Outputs in `--out`:

- `call.wav` — MixMonitor of both voices, started when the extension answers
- `transcript.json` — turns with `t_ms` counted from answer, matching the wav
- `captions.srt` — the same times, labelled `Fernruf (AI)` and `Praxis (AI)`
- `summary.json` — `one_line` and `task_status`
- `demo.mp4` — only with `--video`: 1080x1920, dark background, waveform, burned-in captions, and the line `Both voices are AI. Demo call.`
