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

The callee is a second Pipecat bot (same STT and LLM stack, its own prompt and Fish voice). The default persona is one sentence: the fictional Praxis Beispiel confirms Tuesday at ten. `--persona` replaces that prompt. `FERNRUF_DEMO_CALLEE_VOICE_ID` selects the callee voice and falls back to `FISH_VOICE_ID` when unset.

The default task is one short question, then `end_call`. `--max-seconds` (default 30) starts that same goodbye when the answered call reaches the limit, and hangs up after it has played. The Fernruf side is otherwise a normal call: the fixed AI notice first (using `FERNRUF_PRINCIPAL_NAME`), the identity guard, then the summary.

Recreate Asterisk once so it loads the `fernruf-demo` context, the `fernruf-callee` websocket client, and the monitor volume:

```bash
mkdir -p data/asterisk-sounds data/asterisk-generated data/asterisk-monitor
docker compose up -d --force-recreate asterisk
```

The callee listens on `ws://127.0.0.1:8766/media` (`FERNRUF_DEMO_CALLEE_WS_PORT`). Asterisk uses `FERNRUF_DEMO_CALLEE_WS_URI` (the same default). Restart Asterisk after changing either one. Run the command from the repo root so it can read `data/asterisk-monitor`.

```bash
uv run fernruf demo-call --out data/demo/launch --video
```

`--video` needs ffmpeg (libx264 and aac) and Pillow. It draws a 1080x1920 phone screen: the fictional contact Praxis Beispiel, a plain avatar circle, a running timer, chat bubbles for `Fernruf (AI)` and `Praxis (AI)` timed from `captions.srt`, and a small speaking dot on the active bubble. `Both voices are AI · Demo call` is on every frame, including the first. The mp4 drops the leading ring and silence and starts at the AI notice. `call.wav` and `captions.srt` still match the full MixMonitor, including that lead-in.

Outputs in `--out`:

- `call.wav` — MixMonitor of both voices, started when the extension answers
- `transcript.json` — turns with `t_ms` counted from answer, matching the wav
- `captions.srt` — the same times, labelled `Fernruf (AI)` and `Praxis (AI)`
- `summary.json` — `one_line` and `task_status`
- `demo.mp4` — only with `--video`: 1080x1920 phone screen, chat bubbles, and the line `Both voices are AI · Demo call`
