# Shared helpers for verify-run.sh and CI policy checks (source, do not execute).
verify_common_init() {
  : "${ROOT:?ROOT must be set before sourcing verify/common.sh}"
  SIP_USER="${SIP_USER:-10012345}"
  SIP_PASS="${SIP_PASSWORD:-testpass1234}"
  REGISTRAR_PORT="${VERIFY_SIP_PORT:-5062}"
  SIPP_MEDIA_PORT="${VERIFY_SIPP_MEDIA_PORT:-6002}"
  TEST_E164="${VERIFY_TEST_E164:-+499999999999}"
  VERIFY_ENV_FILE="${VERIFY_ENV_FILE:-$ROOT/.env.verify}"
  POLICY_ALLOWLIST="${TEST_E164},+499001234567,+491180000000,+491713920011"
  GW="${VERIFY_SIP_HOST:-127.0.0.1}"
}

verify_expect_reject() {
  local name="$1"
  local expect_reason="$2"
  shift 2
  local out rc
  set +e
  out=$("$@" 2>&1)
  rc=$?
  set -e
  if [[ "$rc" -eq 127 ]]; then
    echo "VERIFY_FAIL: ${name}: command not found ($*)" >&2
    return 1
  fi
  if [[ "$rc" -eq 0 ]]; then
    echo "VERIFY_FAIL: ${name}: expected policy REJECT (exit 2), but command succeeded: ${out}" >&2
    return 1
  fi
  if [[ "$rc" -ne 2 ]]; then
    echo "VERIFY_FAIL: ${name}: expected policy-check exit 2, got ${rc}: ${out}" >&2
    return 1
  fi
  if [[ "$out" != *"REJECT"* ]]; then
    echo "VERIFY_FAIL: ${name}: expected REJECT line in output, got: ${out}" >&2
    return 1
  fi
  if [[ -n "$expect_reason" && "$out" != *"${expect_reason}"* ]]; then
    echo "VERIFY_FAIL: ${name}: expected reason containing '${expect_reason}', got: ${out}" >&2
    return 1
  fi
  echo "VERIFY_OK: ${name} rejected as expected"
}

verify_write_env_file() {
  local evidence_dir="${1:-$ROOT/verify-evidence}"
  mkdir -p "$evidence_dir"
  cat >"$VERIFY_ENV_FILE" <<EOF
SIP_HOST=${GW}
SIP_IP=${GW}
SIP_PORT=${REGISTRAR_PORT}
SIP_USER=${SIP_USER}
SIP_PASSWORD=${SIP_PASS}
LAN_CIDR=127.0.0.0/8
PJSIP_REG_EXPIRATION=7200
PJSIP_QUALIFY_FREQUENCY=0
ASTERISK_ARI_USER=fernruf
ASTERISK_ARI_PASSWORD=fernruf-verify
ASTERISK_ARI_URL=http://127.0.0.1:8088
FERNRUF_ALLOWLIST=${TEST_E164}
FERNRUF_PRINCIPAL_NAME=Verify Operator
FERNRUF_LLM_MODEL=openrouter/test-model
FERNRUF_INTERNATIONAL_ENABLED=false
FERNRUF_MAX_MINUTES=10
FERNRUF_QUIET_HOURS=off
FERNRUF_QUIET_HOURS_ALLOW_OFF=true
FERNRUF_LLM_MOCK=true
FERNRUF_VOICE_MOCK=false
FERNRUF_FISH_TTS_REST_FIXTURE=${ROOT}/tests/fixtures/fish_tts_de.wav
FERNRUF_WHISPER_MODEL=tiny
FERNRUF_WHISPER_DEVICE=cpu
FERNRUF_WHISPER_COMPUTE_TYPE=int8
FERNRUF_VERIFY_STT_FIXTURE=Hallo, ich höre Sie gut.
FERNRUF_SETTINGS_DB=${evidence_dir}/fernruf-verify.sqlite
FERNRUF_LATENCY_LOG=${evidence_dir}/m2-loopback-latency.jsonl
FERNRUF_WS_HOST=127.0.0.1
FERNRUF_WS_PORT=8765
FERNRUF_WS_URI=ws://127.0.0.1:8765/media
EOF
}

verify_fernruf_env() {
  env FERNRUF_ENV_FILE="$VERIFY_ENV_FILE" "$@"
}

verify_run_policy_pchecks() {
  verify_common_init
  verify_write_env_file "${1:-$ROOT/verify-evidence}"
  local PCHECK=(
    verify_fernruf_env env
    FERNRUF_INTERNATIONAL_ENABLED=true
    "FERNRUF_ALLOWLIST=${POLICY_ALLOWLIST}"
  )
  verify_expect_reject "blocked internal **99" "internal_star_dial" \
    "${PCHECK[@]}" uv run fernruf policy-check "**99" || return 1
  verify_expect_reject "blocked sonder 0900" "blocked_prefix" \
    "${PCHECK[@]}" uv run fernruf policy-check "+499001234567" || return 1
  verify_expect_reject "blocked 118xx" "blocked_prefix" \
    "${PCHECK[@]}" uv run fernruf policy-check "+491180000000" || return 1
  verify_expect_reject "quiet hours evening (explicit 20:00-08:00)" "quiet_hours" \
    verify_fernruf_env env FERNRUF_INTERNATIONAL_ENABLED=true \
      "FERNRUF_ALLOWLIST=${POLICY_ALLOWLIST}" \
      FERNRUF_QUIET_HOURS=20:00-08:00 \
      uv run fernruf policy-check "+491713920011" --at-iso "2026-06-15T21:00:00+02:00" || return 1
}
