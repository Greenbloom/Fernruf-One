# Voice latency notes (M2)

Per-turn latency is logged as JSONL (`FERNRUF_LATENCY_LOG`) and INFO lines for
**STT end → LLM first token → Fish TTFA → first audio out** (see `ConversationMetricsProcessor`).

## M2 hangup and identity

- **Call phase** (`opening` → `talking` → `ending` → `hung_up`) is the only end-call state. Hangup waits for phone playout (`QUEUE_DRAINED`, else the output playout clock), not for `BotStoppedSpeaking`. See [call-flow.md](call-flow.md).
- A blocked `end_call` returns `run_llm=false` and does not speak again. A spoken farewell (`Auf Wiederhören`, `Tschüss`) after the callee has answered ends the call the same way.
- **Identity guard** rewrites assistant text that falsely claims to be the principal or a human before TTS.
- **Ctrl-C** on live M2 uses `external_shutdown` + graceful pipeline teardown (see `tests/test_m2_sigint_shutdown.py`).

## Fish Audio REST (M2 default)

`FishRestTTSService` uses **HTTP streaming** on `POST /v1/tts` (model header e.g. `s2.1-pro-free`): PCM arrives in chunks as synthesis runs, and pipecat gets `TTSAudioRawFrame`s as soon as aligned 16-bit samples are available (no full-body buffer wait).

Env tuning:

- `FERNRUF_FISH_LATENCY` — `balanced` (default), `low`, or `normal` (Fish request body `latency`). Example cold `fish-smoke` medians on one dev host with `s2.1-pro-free` were about **617 ms** (`low`) and **584 ms** (`balanced`); your numbers will vary. In-call reuse of the shared client should beat one-off smoke runs.
- `FERNRUF_FISH_CHUNK_LENGTH` — `100`–`300` (default `100`; Fish `chunk_length`).

Logs:

- `Fish REST TTFA …ms` per sentence in the Fish client.
- `Fish TTS time-to-first-audio …ms` in `fernruf.voice.tts`.

Smoke test: `uv run fernruf fish-smoke "Hallo"` prints `ttfa_ms` in JSON.

A shared `httpx.AsyncClient` is reused across sentences in one process.

### Free-tier speedups (P9)

Env flags (defaults tuned for `s2.1-pro-free`):

- `FERNRUF_FISH_FIRST_CLAUSE=1` — send the first comma/clause chunk (≥ ~4 words) to Fish before the rest of the sentence.
- Pipelining — while sentence *N* plays, sentence *N+1* is fetched (max two HTTP streams in flight); barge-in cancels in-flight synthesis.
- Disk cache under `data/fish-tts-cache/` for fixed phrases (AI disclosure opener, filler, standard goodbyes), keyed by language+text+voice+model+latency.
- Warm `POST /v1/tts` with `"."` at call start to keep HTTP/2 hot.

Per-turn INFO/JSONL chain: **STT final → LLM first token → Fish TTFA → first RTP out**.

### WebSocket transport (P8, optional)

- `FERNRUF_FISH_TRANSPORT=rest|websocket` (default `rest`), `FERNRUF_FISH_MODEL` (default `s2.1-pro-free`).
- WebSocket mode uses one live Fish session per call; on model rejection or connect failure, logs WARNING and falls back to REST for the remainder of the call.

## Other levers

1. **Shorter first sentence** — trim the opener/first LLM sentence so the first Fish request is smaller.
2. **Shorter filler phrases** — keep `FERNRUF_FILLER_PHRASE` brief so the first Fish request stays small.
3. **Fish `/v1/tts/stream-with-timestamps` (SSE)** — not wired yet; optional if REST streaming is insufficient.

Measure before/after using latency JSONL medians and `fish-smoke` TTFA on the host.
