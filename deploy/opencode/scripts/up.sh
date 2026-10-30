#!/bin/sh
# Start the appliance, installing the one-time AppArmor profile if needed.
#
#   deploy/opencode/scripts/up.sh
#   deploy/opencode/scripts/up.sh --build
#
# AppArmor cannot be dropped without either breaking OpenCode's sandbox
# preflight or running the container privileged. This wrapper keeps it a
# one-time, invisible step: it checks the profile, installs it with sudo if
# missing, then runs `docker compose up -d`.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$ROOT"
COMPOSE="$ROOT/deploy/opencode/compose.yaml"
PROFILE=kirocrew-container
PROFILES=/sys/kernel/security/apparmor/profiles

if command -v apparmor_parser >/dev/null 2>&1; then
    if [ -r "$PROFILES" ] && grep -q "^$PROFILE " "$PROFILES"; then
        :
    else
        echo "== installing the $PROFILE AppArmor profile (one-time) =="
        sudo "$ROOT/deploy/opencode/scripts/install-host.sh"
    fi
else
    echo "WARNING: AppArmor is not available on this host." >&2
    echo "Kiro Crew's OpenCode harness refuses to run unsandboxed; the container" >&2
    echo "will start, but agent sessions may be refused. Use a host with AppArmor" >&2
    echo "or see deploy/opencode/README.md for the security trade-off." >&2
fi

exec docker compose -f "$COMPOSE" up -d "$@"
