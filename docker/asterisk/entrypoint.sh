#!/usr/bin/env bash
set -euo pipefail

GEN=/etc/asterisk/generated
TPL=/opt/fernruf/templates

export SIP_HOST="${SIP_HOST:-192.0.2.1}"
export SIP_IP="${SIP_IP:-192.0.2.1}"
export SIP_USER="${SIP_USER:?SIP_USER required}"
export SIP_PASSWORD="${SIP_PASSWORD:?SIP_PASSWORD required}"
export LAN_CIDR="${LAN_CIDR:-192.0.2.0/24}"
export SIP_PORT="${SIP_PORT:-5060}"
export PJSIP_REG_EXPIRATION="${PJSIP_REG_EXPIRATION:-300}"
export PJSIP_QUALIFY_FREQUENCY="${PJSIP_QUALIFY_FREQUENCY:-60}"
export ASTERISK_ARI_USER="${ASTERISK_ARI_USER:-fernruf}"
export ASTERISK_ARI_PASSWORD="${ASTERISK_ARI_PASSWORD:?ASTERISK_ARI_PASSWORD required}"
export FERNRUF_CALLER_ID_NAME="${FERNRUF_CALLER_ID_NAME:-Fernruf}"
export FERNRUF_CALLER_ID_NUMBER="${FERNRUF_CALLER_ID_NUMBER:-100}"
export FERNRUF_WS_URI="${FERNRUF_WS_URI:-ws://127.0.0.1:8765/media}"

if [ -z "$ASTERISK_ARI_PASSWORD" ] || [ "$ASTERISK_ARI_PASSWORD" = "change-me-ari-secret" ] || [ "$ASTERISK_ARI_PASSWORD" = "fernruf-dev" ]; then
  echo "FATAL: set ASTERISK_ARI_PASSWORD to a strong secret (e.g. openssl rand -hex 16), not a placeholder." >&2
  exit 1
fi

mkdir -p "$GEN"

for f in pjsip.conf extensions.conf ari.conf http.conf websocket_client.conf; do
  envsubst <"$TPL/${f}.template" >"$GEN/$f"
done
cp "$TPL/rtp.conf" "$GEN/rtp.conf"

# Include generated snippets from main configs shipped in the image.
for base in pjsip.conf extensions.conf ari.conf http.conf rtp.conf; do
  if [[ -f /etc/asterisk/$base ]] && ! grep -q "fernruf-generated" "/etc/asterisk/$base" 2>/dev/null; then
    echo "; fernruf-generated" >>"/etc/asterisk/$base"
    echo "#include $GEN/$base" >>"/etc/asterisk/$base"
  elif [[ ! -f /etc/asterisk/$base ]]; then
    cp "$GEN/$base" "/etc/asterisk/$base"
  else
    cp "$GEN/$base" "/etc/asterisk/$base"
  fi
done

# Image ships defaults; overwrite with rendered configs.
cp "$GEN/pjsip.conf" /etc/asterisk/pjsip.conf
cp "$GEN/extensions.conf" /etc/asterisk/extensions.conf
cp "$GEN/ari.conf" /etc/asterisk/ari.conf
cp "$GEN/http.conf" /etc/asterisk/http.conf
cp "$GEN/rtp.conf" /etc/asterisk/rtp.conf
cp "$GEN/websocket_client.conf" /etc/asterisk/websocket_client.conf
echo "load => chan_websocket.so" >/etc/asterisk/fernruf-modules.conf
grep -q fernruf-modules.conf /etc/asterisk/modules.conf 2>/dev/null || echo "#include fernruf-modules.conf" >>/etc/asterisk/modules.conf

exec /usr/sbin/asterisk -f -U root -G root
