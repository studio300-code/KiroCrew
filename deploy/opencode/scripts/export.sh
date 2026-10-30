#!/bin/sh
# Export the built image as a transferable gzipped tar.
#
#   deploy/opencode/scripts/export.sh [output-dir]
#
# On the private server:
#   gunzip -c kirocrew-opencode-<version>.tar.gz | docker load
#   docker compose up -d

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
ENV_FILE="$ROOT/deploy/opencode/.env"
OUT_DIR=${1:-$ROOT/dist}
mkdir -p "$OUT_DIR"

get_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

IMAGE=${KIROCREW_IMAGE:-$(get_env KIROCREW_IMAGE)}
IMAGE=${IMAGE:-kirocrew-opencode:local}
VERSION=$(python3 - "$ROOT/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    print(tomllib.load(fh)["project"]["version"])
PY
)
OUT="$OUT_DIR/kirocrew-opencode-${VERSION}.tar.gz"

echo "exporting $IMAGE -> $OUT"
docker save "$IMAGE" | gzip -9 > "$OUT"
ls -lh "$OUT"
