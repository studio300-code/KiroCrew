#!/bin/sh
# One real model turn inside the container, then read the model the session used.
#
#   deploy/opencode/scripts/live-check.sh
#
# Runs the request from 127.0.0.1 inside the container so it satisfies the
# dashboard's local-token peer binding. Good for verifying OpenRouter/OpenCode
# credentials and the seeded model without opening a browser.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"
SLOT=${1:-live-check-$(date +%s)}

docker compose -f "$COMPOSE" exec -T kirocrew sh -s "$SLOT" <<'SH'
set -eu
SLOT=$1
TOKEN=$(kirocrew token --ttl 10m 2>/dev/null | sed -n 's/.*token=//p' | head -n 1)
if [ -z "$TOKEN" ]; then
    echo "could not mint a dashboard token" >&2
    exit 1
fi
echo "== assistant =="
curl -N -s --max-time 180 -X POST "http://127.0.0.1:5476/api/chat?token=$TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"message\":\"Rispondi solo con la parola OK.\",\"slot\":\"$SLOT\"}" \
    | sed -n 's/^data: //p' \
    | jq -r 'select(.type=="chunk")|.content' 2>/dev/null \
    | tr -d '\n'
echo
echo "== turn stats =="
tail -n 1 "$HOME/.kiro/crew/sessions/dashboard_${SLOT}.jsonl" | jq -c '.meta.turn_stats'
SH
