#!/bin/sh
set -eu

# Generate LiveKit server config from env so API keys match voice-agent / SIP.
KEY="${LIVEKIT_API_KEY:?LIVEKIT_API_KEY must be set}"
SECRET="${LIVEKIT_API_SECRET:?LIVEKIT_API_SECRET must be set}"
NODE_IP="${LIVEKIT_NODE_IP:-}"

if [ "${#SECRET}" -lt 32 ]; then
  echo "ERROR: LIVEKIT_API_SECRET must be at least 32 characters (got ${#SECRET})." >&2
  echo "Generate one with: openssl rand -hex 32" >&2
  exit 1
fi

USE_EXTERNAL_IP="false"
NODE_IP_LINE=""
if [ -n "$NODE_IP" ]; then
  USE_EXTERNAL_IP="true"
  NODE_IP_LINE="  node_ip: \"${NODE_IP}\""
fi

cat > /tmp/livekit.yaml <<EOF
port: 7880
rtc:
  port_range_start: 50000
  port_range_end: 50200
  use_external_ip: ${USE_EXTERNAL_IP}
${NODE_IP_LINE}
  tcp_port: 7881

turn:
  enabled: true
  domain: ""
  tls_port: 0
  udp_port: 7882
  external_tls: false

keys:
  ${KEY}: ${SECRET}

logging:
  level: info

redis:
  address: ${LIVEKIT_REDIS_ADDRESS:-redis:6379}
  password: "${LIVEKIT_REDIS_PASSWORD:-}"
  db: ${LIVEKIT_REDIS_DB:-1}
EOF

echo "LiveKit config written (key=${KEY}, secret_len=${#SECRET}, node_ip=${NODE_IP:-unset})"
exec /livekit-server --config /tmp/livekit.yaml
