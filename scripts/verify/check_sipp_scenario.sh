#!/usr/bin/env bash
# Loopback SIPp check: UAS registrar + UAC REGISTER (good/bad) + UAC INVITE (127.0.0.1 only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCEN="$ROOT/scripts/verify/scenarios"
UAS="$SCEN/sip_registrar_uas.xml"
UAC_REG="$SCEN/uac_reg.xml"
UAC_INV="$SCEN/uac_inv.xml"
SIP_USER="${SIP_USER:-10012345}"
SIP_PASS="${SIP_PASSWORD:-testpass1234}"
UAS_PORT="${VERIFY_SIP_PORT:-5070}"
UAS_MEDIA="${VERIFY_SIPP_MEDIA_PORT:-6002}"
MSG="$ROOT/scripts/verify/fixtures/.sipp-loopback-messages.log"
UAS_LOG="$ROOT/scripts/verify/fixtures/.sipp-loopback-uas.log"
UAS_PID=""
trap 'rm -f "$MSG" "$UAS_LOG"; [[ -n "$UAS_PID" ]] && kill "$UAS_PID" 2>/dev/null || true' EXIT

command -v sipp >/dev/null || { echo "missing sipp (install sip-tester)" >&2; exit 1; }

export PATH="${HOME}/.local/bin:${PATH}"
cd "$ROOT"
if command -v uv >/dev/null; then
  uv run python "$ROOT/scripts/gen_verify_callee_fixture.py" >/dev/null
else
  python3 "$ROOT/scripts/gen_verify_callee_fixture.py" >/dev/null
fi

if ! [[ -f "$ROOT/scripts/verify/fixtures/callee-hallo-rtp.pcap" ]]; then
  echo "missing RTP fixture after gen_verify_callee_fixture.py" >&2
  exit 1
fi

killall -9 sipp 2>/dev/null || true
sleep 1
rm -f "$MSG" "$UAS_LOG"
: >"$MSG"
: >"$UAS_LOG"
chmod a+rw "$MSG" "$UAS_LOG"

if sipp -h 2>&1 | grep -qE '(^|[[:space:]])-mp[[:space:]]'; then
  MEDIA=( -mi 127.0.0.1 -mp "$UAS_MEDIA" )
else
  MEDIA=( -mi 127.0.0.1 -min_rtp_port "$UAS_MEDIA" -max_rtp_port "$UAS_MEDIA" )
fi

sudo sipp -sf "$UAS" -i 127.0.0.1 -p "$UAS_PORT" "${MEDIA[@]}" \
  -key sipuser "$SIP_USER" -key sippass "$SIP_PASS" \
  -l 8 -timeout 120s \
  -trace_msg -message_file "$MSG" -message_overwrite 1 >"$UAS_LOG" 2>&1 &
UAS_PID=$!
sleep 2
kill -0 "$UAS_PID" || { cat "$UAS_LOG" >&2; exit 1; }

uac_reg() {
  local pass="$1"
  local expect_ok="$2"
  local port="$3"
  set +e
  sipp -sf "$UAC_REG" "127.0.0.1:${UAS_PORT}" -i 127.0.0.1 -p "$port" -m 1 -timeout 15s \
    -key sipuser "$SIP_USER" -key sippass "$pass" \
    -au "$SIP_USER" -ap "$pass" 2>&1
  local rc=$?
  set -e
  if [[ "$expect_ok" == "yes" && "$rc" -ne 0 ]]; then
    echo "UAC REGISTER (good creds) failed rc=$rc" >&2
    exit 1
  fi
  if [[ "$expect_ok" == "no" && "$rc" -eq 0 ]]; then
    echo "UAC REGISTER (bad creds) unexpectedly succeeded" >&2
    exit 1
  fi
}

uac_reg "$SIP_PASS" yes 5071
grep -q "401 Unauthorized" "$MSG" || { echo "missing 401 in message trace" >&2; exit 1; }
grep -q "Authorization: Digest" "$MSG" || { echo "missing Authorization: Digest in message trace" >&2; exit 1; }

uac_reg "wrong-password" no 5073
grep -q "403 Forbidden" "$MSG" || { echo "missing 403 for bad REGISTER password" >&2; exit 1; }

set +e
sipp -sf "$UAC_INV" "127.0.0.1:${UAS_PORT}" -i 127.0.0.1 -p 5072 -m 1 -timeout 45s \
  -min_rtp_port 17000 -max_rtp_port 17000 2>&1
inv_rc=$?
set -e
if [[ "$inv_rc" -ne 0 ]]; then
  echo "UAC INVITE loopback failed rc=$inv_rc" >&2
  cat "$UAS_LOG" >&2
  exit 1
fi
grep -q "INVITE sip:" "$MSG" || { echo "missing INVITE in message trace" >&2; exit 1; }

echo "sipp loopback ok (digest REGISTER, 403 on bad pass, INVITE/BYE)"
