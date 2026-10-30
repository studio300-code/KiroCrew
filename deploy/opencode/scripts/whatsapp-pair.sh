#!/bin/sh
# Pair the container's WhatsApp device with an 8-character phone code.
#
#   deploy/opencode/scripts/whatsapp-pair.sh 393451234567
#
# The dashboard pairing endpoints are local-machine-only by design. A browser
# reaching a published Docker port is not local, so this runs the request from
# inside the container (127.0.0.1) and prints the code. On the phone:
# WhatsApp -> Settings -> Linked devices -> Link a device -> Link with phone
# number instead, then enter the code.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"
PHONE=${1:-}
if [ -z "$PHONE" ]; then
    echo "usage: $0 <phone-with-country-code-digits>" >&2
    exit 2
fi

docker compose -f "$COMPOSE" exec -T kirocrew sh -s "$PHONE" <<'SH'
set -eu
PHONE=$1
URL=$(kirocrew token --ttl 5m 2>/dev/null | sed -n 's/.*token=//p' | head -n 1)
if [ -z "$URL" ]; then
    echo "could not mint a dashboard token" >&2
    exit 1
fi
curl -fsS -X POST "http://127.0.0.1:${KIROCREW_PORT:-5476}/api/channels/whatsapp/pair-phone?token=$URL" \
    -H 'Content-Type: application/json' \
    -d "{\"phone\":\"$PHONE\"}" | jq -r 'if .ok then .code else .error end'
SH
