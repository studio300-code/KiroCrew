#!/bin/sh
# Build the wheel from this checkout and the appliance image.
#
#   deploy/opencode/scripts/build.sh
#
# Requires Docker with BuildKit (Docker 23+ default) and Python 3.12 with pip.
# The dashboard is built only when website/dist is missing; otherwise the
# checked-in build is used exactly as the running gateway uses it.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
ENV_FILE="$ROOT/deploy/opencode/.env"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"

get_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

if [ ! -f "$ROOT/website/dist/index.html" ]; then
    echo "== dashboard build missing; building website =="
    if ! command -v npm >/dev/null 2>&1; then
        echo "website/dist is missing and npm is not installed" >&2
        exit 1
    fi
    (cd "$ROOT/website" && npm ci && npm run build)
fi

# Make sure the package-data path points at the built dashboard. It is a
# symlink in the checkout and must exist before the wheel is built.
PYTHON=${PYTHON:-python3}
if [ ! -e "$ROOT/src/kiro_crew/static/dist/index.html" ]; then
    ln -sfn "$ROOT/website/dist" "$ROOT/src/kiro_crew/static/dist"
fi

echo "== building KiroCrew wheel =="
mkdir -p "$ROOT/dist"
rm -f "$ROOT/dist"/kirocrew-*.whl
"$PYTHON" -m pip wheel "$ROOT" --no-deps -w "$ROOT/dist"
WHEEL=$(ls "$ROOT"/dist/kirocrew-*.whl | head -n 1)
echo "wheel: $WHEEL"

VERSION=$("$PYTHON" - "$ROOT/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
)

echo "== building image =="
docker compose -f "$COMPOSE" build --pull

IMAGE=$(get_env KIROCREW_IMAGE)
IMAGE=${IMAGE:-kirocrew-opencode:local}
docker tag "$IMAGE" "kirocrew-opencode:${VERSION}" 2>/dev/null || true
echo "image: $IMAGE (also kirocrew-opencode:${VERSION})"
