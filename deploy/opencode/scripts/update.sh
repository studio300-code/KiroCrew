#!/bin/sh
# Update the checkout, rebuild the wheel/image, and recreate the container.
#
#   deploy/opencode/scripts/update.sh
#
# The named volume is never touched. Local commits are rebased onto upstream
# origin/main exactly like LOCAL.md documents for the ASUS install. A dirty
# checkout is refused so no work is lost.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "refusing to update: the checkout has uncommitted changes" >&2
    echo "commit or stash them first" >&2
    exit 1
fi

echo "== fetching upstream =="
git fetch origin
if git merge-base --is-ancestor origin/main HEAD; then
    echo "already up to date with origin/main"
else
    echo "== rebasing local commits onto origin/main =="
    git rebase origin/main
fi

echo "== rebuilding =="
"$ROOT/deploy/opencode/scripts/build.sh"

echo "== recreating the container =="
docker compose -f "$COMPOSE" up -d
docker compose -f "$COMPOSE" ps
