#!/usr/bin/env bash
# Render Asterisk configs locally (same templates as the Docker entrypoint).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/data/asterisk-generated}"
TPL="$ROOT/docker/asterisk/templates"
mkdir -p "$OUT"

: "${SIP_USER:?set SIP_USER}"
: "${SIP_PASSWORD:?set SIP_PASSWORD}"

export SIP_HOST="${SIP_HOST:-192.0.2.1}"
export SIP_IP="${SIP_IP:-192.0.2.1}"
export LAN_CIDR="${LAN_CIDR:-192.0.2.0/24}"
export ASTERISK_ARI_USER="${ASTERISK_ARI_USER:-fernruf}"
: "${ASTERISK_ARI_PASSWORD:?set ASTERISK_ARI_PASSWORD}"
export ASTERISK_ARI_PASSWORD
export FERNRUF_WS_URI="${FERNRUF_WS_URI:-ws://127.0.0.1:8765/media}"
export FERNRUF_DEMO_CALLEE_WS_URI="${FERNRUF_DEMO_CALLEE_WS_URI:-ws://127.0.0.1:8766/media}"

for f in pjsip.conf extensions.conf ari.conf http.conf websocket_client.conf; do
  envsubst <"$TPL/${f}.template" >"$OUT/$f"
done
cat "$TPL/extensions.demo.conf" >>"$OUT/extensions.conf"
cp "$TPL/rtp.conf" "$OUT/rtp.conf"
echo "Rendered configs in $OUT"
