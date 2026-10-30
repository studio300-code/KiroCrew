#!/bin/sh
# Fresh-volume smoke test.
#
#   deploy/opencode/scripts/smoke.sh
#   KEEP=1 deploy/opencode/scripts/smoke.sh   # leave the test container running
#
# Uses a separate compose project, container name and port, and removes its
# volume on exit unless KEEP=1. It never touches a real deployment.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"
PROJECT=${SMOKE_PROJECT:-kirocrew-opencode-smoke}
CTR=${SMOKE_CONTAINER:-kirocrew-opencode-smoke}
PORT=${SMOKE_PORT:-15476}
export KIROCREW_CONTAINER_NAME="$CTR"
export KIROCREW_PORT="$PORT"
export KIROCREW_BIND_HOST=127.0.0.1
export KIROCREW_PUBLIC_URL="${KIROCREW_PUBLIC_URL:-http://127.0.0.1:$PORT}"

cleanup() {
    if [ "${KEEP:-0}" != "1" ]; then
        docker compose -p "$PROJECT" -f "$COMPOSE" down -v >/dev/null 2>&1 || true
    else
        echo "kept container $CTR on http://127.0.0.1:$PORT"
    fi
}
trap cleanup EXIT INT TERM

echo "== starting $PROJECT =="
docker compose -p "$PROJECT" -f "$COMPOSE" up -d --build

status=missing
i=0
while [ "$i" -lt 90 ]; do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CTR" 2>/dev/null || echo missing)
    [ "$status" = "healthy" ] && break
    [ "$status" = "exited" ] && break
    i=$((i + 1))
    sleep 2
done

if [ "$status" != "healthy" ]; then
    echo "container did not become healthy (status=$status)" >&2
    docker logs "$CTR" 2>&1 | tail -80 >&2 || true
    exit 1
fi
echo "health: healthy"

echo "== binaries =="
docker exec "$CTR" opencode --version
docker exec "$CTR" ffmpeg -version 2>/dev/null | head -n 1
docker exec "$CTR" python3 -c 'import neonize, pywhispercpp; print("neonize+pywhispercpp ok")'

echo "== seeded config =="
docker exec "$CTR" kirocrew config get agent.acp_backend
docker exec "$CTR" kirocrew config get agent.model
docker exec "$CTR" kirocrew config get agent.reasoning_effort
docker exec "$CTR" kirocrew config get agent.role_models
docker exec "$CTR" kirocrew config get agent.role_efforts
docker exec "$CTR" kirocrew config get stt.model
docker exec "$CTR" sh -c 'grep -q "nemotron-3-ultra" "$HOME/.config/opencode/opencode.jsonc" && echo opencode-config-ok'
docker exec "$CTR" sh -c 'test -f "$HOME/.local/share/opencode/auth.json" && echo opencode-auth-ok || echo opencode-auth-missing'

echo "== dashboard token =="
docker exec "$CTR" kirocrew token --ttl 5m | head -n 1

IMAGE=$(docker inspect -f '{{.Config.Image}}' "$CTR")
echo "== image has no personal data =="
docker run --rm --entrypoint sh "$IMAGE" -c '
set -eu
test ! -e /home/kirocrew/whatsapp-web/data/wa.db
test ! -e /home/kirocrew/.kiro/crew/config.json
test ! -e /home/kirocrew/.kiro/crew/sessions
test ! -e /home/kirocrew/.config/opencode/opencode.jsonc
if grep -RIl "sk-or-v1-" /home/kirocrew /usr/local/lib/python3.12/site-packages/kiro_crew 2>/dev/null | grep -q .; then
  echo "credential-like string found in image" >&2; exit 1
fi
if grep -RIl "/home/marco" /home/kirocrew 2>/dev/null | grep -q .; then
  echo "host path found in image home" >&2; exit 1
fi
echo image-clean
'

echo "smoke test passed"
