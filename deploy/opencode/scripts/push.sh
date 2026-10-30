#!/bin/sh
# Tag and push the appliance image to a registry.
#
#   REGISTRY=ghcr.io/your-org deploy/opencode/scripts/push.sh
#
# Login first: echo "$GH_TOKEN" | docker login ghcr.io -u USER --password-stdin

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
ENV_FILE="$ROOT/deploy/opencode/.env"

get_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

REGISTRY=${REGISTRY:-}
if [ -z "$REGISTRY" ]; then
    echo "set REGISTRY, e.g. REGISTRY=ghcr.io/your-org $0" >&2
    exit 1
fi

IMAGE=${KIROCREW_IMAGE:-$(get_env KIROCREW_IMAGE)}
IMAGE=${IMAGE:-kirocrew-opencode:local}
VERSION=$(python3 - "$ROOT/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
)

TARGET="$REGISTRY/kirocrew-opencode"
docker tag "$IMAGE" "$TARGET:$VERSION"
docker tag "$IMAGE" "$TARGET:latest"
docker push "$TARGET:$VERSION"
docker push "$TARGET:latest"
echo "pushed $TARGET:$VERSION and :latest"
