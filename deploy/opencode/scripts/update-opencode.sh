#!/bin/sh
# Pin the latest published OpenCode CLI for both architectures.
#
#   deploy/opencode/scripts/update-opencode.sh          # update .env only
#   deploy/opencode/scripts/update-opencode.sh --build  # update + rebuild image
#
# The registry metadata gives the sha512 of each tarball; the Dockerfile
# verifies it before extracting the binary.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
ENV_FILE="$ROOT/deploy/opencode/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$ROOT/deploy/opencode/.env.example" "$ENV_FILE"
    echo "created $ENV_FILE from .env.example"
fi

PYTHON=${PYTHON:-python3}
"$PYTHON" - "$ENV_FILE" <<'PY'
import base64
import json
import pathlib
import sys
import urllib.request

env_path = pathlib.Path(sys.argv[1])

def fetch_json(url: str) -> dict:
    # The npm registry rejects urllib's default User-Agent with HTTP 403.
    req = urllib.request.Request(url, headers={"User-Agent": "kirocrew-opencode-updater"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)

latest = fetch_json("https://opencode.ai/update/api/latest/cli/npm")
version = str(latest.get("version") or "").strip()
if not version:
    raise SystemExit("latest OpenCode metadata has no version")

values = {"OPENCODE_VERSION": version}
for arch, key in (("cli-linux-x64", "OPENCODE_SHA512_X64"), ("cli-linux-arm64", "OPENCODE_SHA512_ARM64")):
    meta = fetch_json(f"https://registry.npmjs.org/@opencode%2f{arch}/{version}")
    integrity = str(meta.get("dist", {}).get("integrity") or "")
    if not integrity.startswith("sha512-"):
        raise SystemExit(f"{arch} {version}: missing sha512 integrity")
    digest = base64.b64decode(integrity.split("-", 1)[1]).hex()
    values[key] = digest

lines = env_path.read_text(encoding="utf-8").splitlines()
for key, value in values.items():
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"OpenCode {version} pinned in {env_path}")
for key, value in values.items():
    print(f"  {key}={value}")
PY

if [ "${1:-}" = "--build" ]; then
    exec "$ROOT/deploy/opencode/scripts/build.sh"
fi
echo "run deploy/opencode/scripts/build.sh to build the image"
