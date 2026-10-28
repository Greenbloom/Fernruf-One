#!/usr/bin/env bash
# End-to-end loopback verification (same Asterisk image/config as production).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE="${VERIFY_EVIDENCE_DIR:-$ROOT/verify-evidence}"
mkdir -p "$EVIDENCE" "$ROOT/data/asterisk-sounds" "$ROOT/data/asterisk-generated" "$ROOT/data/asterisk-monitor"
: >"$EVIDENCE/run.log"
exec > >(tee -a "$EVIDENCE/run.log") 2>&1

export PATH="$HOME/.local/bin:$PATH"
cd "$ROOT"

VERIFY_FAILURES=()

fail() {
  echo "VERIFY_FAIL: $*" >&2
  exit 1
}

record_fail() {
  VERIFY_FAILURES+=("$1")
  echo "VERIFY_FAIL: $1" >&2
  dump_asterisk_debug "$1"
}

# shellcheck source=scripts/verify/common.sh
source "$ROOT/scripts/verify/common.sh"
verify_common_init

expect_reject() {
  verify_expect_reject "$@" || fail "${1}: policy reject check failed"
}

PCAP="$EVIDENCE/rtp.pcap"
SIPP_MSG="$EVIDENCE/sipp-messages.log"

# RTP on loopback; SIPp 3.7 (Debian) uses -min_rtp_port, newer builds use -mp.
if sipp -h 2>&1 | grep -qE '(^|[[:space:]])-mp[[:space:]]'; then
  SIPP_MEDIA=( -mi "$GW" -mp "$SIPP_MEDIA_PORT" )
else
  SIPP_MEDIA=(
    -mi "$GW"
    -min_rtp_port "$SIPP_MEDIA_PORT"
    -max_rtp_port "$SIPP_MEDIA_PORT"
  )
fi

COMPOSE_PROJECT="${VERIFY_COMPOSE_PROJECT:-fernruf-verify}"

compose() {
  sudo docker compose -p "$COMPOSE_PROJECT" -f docker-compose.yml -f docker-compose.verify.yml "$@"
}

prod_compose() {
  sudo docker compose --env-file "$ROOT/.env" "$@"
}

dump_asterisk_debug() {
  local reason="${1:-unknown}"
  echo "== asterisk debug dump (${reason}) -> verify-evidence/asterisk-*.txt =="
  compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show endpoint sip-trunk" \
    >"$EVIDENCE/asterisk-pjsip-endpoint-sip-trunk.txt" 2>/dev/null || true
  compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show aors" \
    >"$EVIDENCE/asterisk-pjsip-aors.txt" 2>/dev/null || true
  compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show contacts" \
    >"$EVIDENCE/asterisk-pjsip-contacts.txt" 2>/dev/null || true
  compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show registrations" \
    >"$EVIDENCE/asterisk-pjsip-registrations-live.txt" 2>/dev/null || true
  sudo docker compose -f docker-compose.yml -f docker-compose.verify.yml --env-file "$VERIFY_ENV_FILE" \
    logs --tail 200 asterisk >"$EVIDENCE/asterisk-log-tail.txt" 2>/dev/null || true
}

wait_pjsip_registered() {
  local tries=0
  while (( tries < 45 )); do
    if compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show registrations" \
      | grep -q "Registered"; then
      return 0
    fi
    sleep 2
    tries=$((tries + 1))
  done
  return 1
}

start_sipp() {
  local scenario="$1"
  local logfile="$2"
  kill "$SIPP_PID" 2>/dev/null || true
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${REGISTRAR_PORT}/udp" 2>/dev/null || true
  elif pgrep -x sipp >/dev/null 2>&1; then
    killall -9 sipp 2>/dev/null || true
  fi
  sleep 1
  rm -f "$SIPP_MSG"
  : >"$SIPP_MSG"
  chmod a+rw "$SIPP_MSG" 2>/dev/null || true
  sudo sipp -sf "$scenario" -i "$GW" -p "$REGISTRAR_PORT" "${SIPP_MEDIA[@]}" \
    -key sipuser "$SIP_USER" -key sippass "$SIP_PASS" \
    -l 4 -d 120000 -timeout 180s \
    -trace_msg -message_file "$SIPP_MSG" -message_overwrite 1 >"$logfile" 2>&1 &
  SIPP_PID=$!
  sleep 2
}

verify_write_env_file "$EVIDENCE"

fernruf_env() {
  verify_fernruf_env "$@"
}

start_tcpdump() {
  rm -f "$PCAP"
  sudo tcpdump -i any -n -s 0 -w "$PCAP" \
    'udp and (portrange 10000-10100 or port '"$SIPP_MEDIA_PORT"' or port 5060 or port '"$REGISTRAR_PORT"')' \
    2>"$EVIDENCE/tcpdump.log" &
  TCPDUMP_PID=$!
  sleep 1
  if ! kill -0 "$TCPDUMP_PID" 2>/dev/null; then
    record_fail "tcpdump failed to start (see verify-evidence/tcpdump.log)"
    TCPDUMP_PID=""
    return 1
  fi
  echo "tcpdump pid=$TCPDUMP_PID writing $PCAP (link any, see tcpdump.log)"
}

stop_tcpdump() {
  if [[ -n "${TCPDUMP_PID:-}" ]]; then
    sudo kill "$TCPDUMP_PID" 2>/dev/null || true
    wait "$TCPDUMP_PID" 2>/dev/null || true
    TCPDUMP_PID=""
  fi
}

PROD_AST_WAS_RUNNING=0
PROD_AST_CONTAINER=""
if [[ -f "$ROOT/.env" ]]; then
  PROD_AST_CONTAINER="$(prod_compose ps -q asterisk 2>/dev/null | head -1 || true)"
  if [[ -n "$PROD_AST_CONTAINER" ]] && [[ "$PROD_AST_CONTAINER" != "$(sudo docker ps -aq -f name=^fernruf-verify-asterisk$ 2>/dev/null || true)" ]]; then
    PROD_AST_WAS_RUNNING=1
    echo "note: production asterisk was running (container ${PROD_AST_CONTAINER:0:12}); will restore after verify"
  fi
fi

cleanup() {
  set +e
  stop_tcpdump
  if [[ -n "${SIPP_PID:-}" ]]; then kill "$SIPP_PID" 2>/dev/null; fi
  if ((${#VERIFY_FAILURES[@]})); then
    dump_asterisk_debug "cleanup after failures"
  fi
  compose --env-file "$VERIFY_ENV_FILE" down -v 2>/dev/null | tee -a "$EVIDENCE/run.log" || true
  if (( PROD_AST_WAS_RUNNING )); then
    echo "== restoring production asterisk (docker compose --env-file .env) ==" | tee -a "$EVIDENCE/run.log"
    if [[ -f "$ROOT/.env" ]]; then
      if prod_compose up -d asterisk 2>&1 | tee -a "$EVIDENCE/run.log"; then
        echo "production asterisk restore: docker compose up succeeded" | tee -a "$EVIDENCE/run.log"
        for _ in $(seq 1 30); do
          ari_user="${ASTERISK_ARI_USER:-fernruf}"
          ari_pass="${ASTERISK_ARI_PASSWORD:-}"
          ari_basic="$(printf '%s:%s' "$ari_user" "$ari_pass" | base64 | tr -d '\n')"
          if printf 'header = "Authorization: Basic %s"\n' "$ari_basic" \
            | curl -sf -K - "${ASTERISK_ARI_URL:-http://127.0.0.1:8088}/ari/asterisk/info" >/dev/null 2>&1; then
            echo "production ARI on 8088: ok" | tee -a "$EVIDENCE/run.log"
            break
          fi
          sleep 1
        done
      else
        echo "WARN: could not restart production asterisk; run: sudo docker compose --env-file .env up -d asterisk" | tee -a "$EVIDENCE/run.log"
      fi
    fi
  fi
}
trap cleanup EXIT INT TERM

echo "== doctor =="
command -v sipp >/dev/null || fail "missing sipp (install sip-tester)"
command -v uv >/dev/null || fail "missing uv"
command -v tcpdump >/dev/null || fail "missing tcpdump"
sudo -n true 2>/dev/null || fail "sudo required for tcpdump (passwordless sudo: NOPASSWD for tcpdump/docker)"
sudo docker info >/dev/null || fail "docker unavailable"
echo "note: SIPp UAS runs as root only for raw-socket RTP pcap playback on loopback (${GW}); not used on public interfaces."

echo "== verify fixtures =="
fernruf_env uv run python "$ROOT/scripts/gen_verify_callee_fixture.py" | tee "$EVIDENCE/fixtures.log"

echo "== launch SIPp SIP registrar stand-in (REGISTER + INVITE, port ${REGISTRAR_PORT}) =="
SIPP_PID=""
start_sipp "$ROOT/scripts/verify/scenarios/sip_registrar_uas.xml" "$EVIDENCE/sipp.log"

echo "== launch asterisk (verify compose) =="
compose --env-file "$VERIFY_ENV_FILE" build asterisk
compose --env-file "$VERIFY_ENV_FILE" up -d asterisk
sleep 12

echo "== registration check =="
compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "pjsip show registrations" \
  | tee "$EVIDENCE/pjsip-registrations.txt"
grep -q "Registered" "$EVIDENCE/pjsip-registrations.txt" || fail "pjsip registration (expected Registered)"
grep -q "401 Unauthorized" "$SIPP_MSG" \
  || fail "SIPp digest auth (expected 401 in sipp message trace)"
grep -q "Authorization: Digest" "$SIPP_MSG" \
  || fail "SIPp digest auth (expected Authorization: Digest REGISTER in message trace)"

echo "== policy rejects (hermetic FERNRUF_ENV_FILE=.env.verify) =="
verify_run_policy_pchecks "$EVIDENCE" || fail "policy pchecks failed"

echo "== rtp capture =="
start_tcpdump || true

echo "== m1 test call (keep REGISTER alive on sip_registrar_uas; do not swap SIPp yet) =="
fernruf_env uv sync -q
set +e
fernruf_env uv run fernruf m1-test-call --to "$TEST_E164" --min-hold 32 2>"$EVIDENCE/m1-test-call.err" \
  | tee "$EVIDENCE/m1-call.json"
M1_RC=$?
set -e
if [[ "$M1_RC" -ne 0 ]]; then
  if [[ -s "$EVIDENCE/m1-test-call.err" ]]; then
    tail -5 "$EVIDENCE/m1-test-call.err" | tee -a "$EVIDENCE/run.log"
  fi
  record_fail "m1-test-call exited ${M1_RC} (see m1-call.json, m1-test-call.err; common: ARI Allocation failed when pjsip trunk lost REGISTER)"
fi

PCAP_SIZE=$(stat -c%s "$PCAP" 2>/dev/null || echo 0)
echo "rtp.pcap size=${PCAP_SIZE} bytes (capture continues through M2)"

echo "== rtp pcma check M1 segment (pure Python, no tshark) =="
if [[ -s "$PCAP" ]]; then
  set +e
  fernruf_env uv run python "$ROOT/scripts/analyze_rtp_pcap.py" "$PCAP" | tee "$EVIDENCE/rtp-analysis.txt"
  RTP_RC=$?
  set -e
  if ! grep -q "PCMA_PAYLOADS=ok" "$EVIDENCE/rtp-analysis.txt"; then
    record_fail "M1 RTP PCMA check failed (exit ${RTP_RC}); see rtp-analysis.txt"
  fi
else
  record_fail "skipped rtp analysis (empty pcap)"
fi

echo "== m1 dtmf check (optional; M2 send_dtmf is authoritative) =="
if [[ -f "$EVIDENCE/m1-call.json" ]] && grep -Eiq '"dtmf":\s*\[[^]]*[1234]' "$EVIDENCE/m1-call.json"; then
  echo "M1_DTMF=ok (ARI ChannelDtmfReceived)"
elif [[ -f "$EVIDENCE/rtp-analysis.txt" ]] && grep -q "RFC2833_DTMF=ok" "$EVIDENCE/rtp-analysis.txt"; then
  echo "M1_DTMF=ok (RFC2833 in pcap)"
else
  echo "M1_DTMF=skip (see M2 send_dtmf)"
fi

echo "== wait for M1 hangup (idle channels) =="
wait_asterisk_idle() {
  local tries=0
  while (( tries < 30 )); do
    local summary
    summary="$(compose --env-file "$VERIFY_ENV_FILE" exec -T asterisk asterisk -rx "core show channels" 2>/dev/null || true)"
    if echo "$summary" | grep -q "0 active channels"; then
      return 0
    fi
    sleep 1
    tries=$((tries + 1))
  done
  record_fail "asterisk channels not idle before M2 (core show channels)"
  return 1
}
wait_asterisk_idle || true

echo "== recycle SIPp (same sip_registrar_uas.xml; fresh REGISTER for M2 dialog) =="
start_sipp "$ROOT/scripts/verify/scenarios/sip_registrar_uas.xml" "$EVIDENCE/sipp-recycle.log"
if ! wait_pjsip_registered; then
  record_fail "pjsip not Registered after SIPp recycle (see sipp-recycle.log)"
fi

echo "== m2 loopback =="
: >"${EVIDENCE}/m2-loopback-latency.jsonl"
set +e
fernruf_env uv run fernruf m2-verify-loopback --to "$TEST_E164" \
  | tee "$EVIDENCE/m2-loopback.json"
M2_RC=$?
set -e
if ! grep -q '"ok": true' "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2-verify-loopback failed (exit ${M2_RC}); see m2-loopback.json"
fi
if ! test -s "$EVIDENCE/m2-loopback-latency.jsonl"; then
  record_fail "m2-loopback-latency.jsonl missing or empty"
fi
if [[ -f "$EVIDENCE/m2-loopback.json" ]] && ! grep -q "KI-Assistent" "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2 loopback missing AI disclosure"
fi
if [[ -f "$EVIDENCE/m2-loopback.json" ]] && ! grep -Eq '"dtmf_sent"\s*:\s*"12"' "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2 loopback agent send_dtmf (expected dtmf_sent \"12\")"
fi
if [[ -f "$EVIDENCE/m2-loopback.json" ]] && ! grep -q '"callee_rtp_received": true' "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2 loopback callee RTP on WebSocket input (callee_rtp_received false; see m2-loopback.json)"
fi
if [[ -f "$EVIDENCE/m2-loopback.json" ]] && ! grep -q '"callee_transcribed": true' "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2 loopback callee turn (callee_transcribed false; STT gated on received audio)"
fi
if [[ -f "$EVIDENCE/m2-loopback.json" ]] && ! grep -q '"agent_audio_out": true' "$EVIDENCE/m2-loopback.json"; then
  record_fail "m2 loopback agent audio on WS output (agent_audio_out false)"
fi
PIPECAT_ERRS=$(grep -cE '\| ERROR    \| pipecat|can no longer do its job|ERROR fernruf\.voice\.|FishRestTTSService#0 error' "$EVIDENCE/run.log" 2>/dev/null || true)
PIPECAT_ERRS="${PIPECAT_ERRS:-0}"
echo "PIPECAT_ERROR_LINES=${PIPECAT_ERRS}"
if [[ "$PIPECAT_ERRS" -gt 0 ]]; then
  record_fail "pipecat/service ERROR lines in verify log (count=${PIPECAT_ERRS})"
fi

stop_tcpdump
PCAP_SIZE=$(stat -c%s "$PCAP" 2>/dev/null || echo 0)
echo "rtp.pcap final size=${PCAP_SIZE} bytes"
echo "== M2 callee RTP from SIPp (port ${SIPP_MEDIA_PORT}) =="
if [[ -s "$PCAP" ]]; then
  set +e
  fernruf_env uv run python "$ROOT/scripts/analyze_rtp_pcap.py" "$PCAP" --udp-port "$SIPP_MEDIA_PORT" \
    | tee "$EVIDENCE/m2-sipp-rtp-analysis.txt"
  M2_RTP_RC=$?
  set -e
  if ! grep -q "PCMA_PAYLOADS=ok" "$EVIDENCE/m2-sipp-rtp-analysis.txt"; then
    record_fail "M2 SIPp callee RTP check failed (exit ${M2_RTP_RC}); see m2-sipp-rtp-analysis.txt"
  fi
else
  record_fail "M2 RTP analysis skipped (empty pcap)"
fi

echo "== m2 simulate (offline sanity) =="
set +e
fernruf_env env FERNRUF_LATENCY_LOG="${EVIDENCE}/m2-simulate-latency.jsonl" \
  uv run fernruf m2-test-call --to "$TEST_E164" --task "Verify M2 simulate" --simulate \
  | tee "$EVIDENCE/m2-simulate.json"
M2_SIM_RC=$?
set -e
if [[ "$M2_SIM_RC" -ne 0 ]] || ! grep -q "KI-Assistent" "$EVIDENCE/m2-simulate.json"; then
  record_fail "m2 simulate failed (exit ${M2_SIM_RC})"
fi

wait "$SIPP_PID" 2>/dev/null || true

if ((${#VERIFY_FAILURES[@]})); then
  echo "== verify summary (FAILURES) =="
  for f in "${VERIFY_FAILURES[@]}"; do
    echo "  - $f"
  done
  fail "verify finished with ${#VERIFY_FAILURES[@]} failure(s); see verify-evidence/"
fi

echo "VERIFY_FERNRUF=pass" | tee "$EVIDENCE/summary.txt"
echo "M1_DTMF_NOTE: callee DTMF via SIPp is best-effort; agent send_dtmf verified in M2 loopback"
