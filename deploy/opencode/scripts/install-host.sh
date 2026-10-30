#!/bin/sh
# One-time host setup: load the AppArmor profile that lets the container build
# its inner user-namespace sandbox.
#
#   sudo deploy/opencode/scripts/install-host.sh
#
# This is required on hosts where kernel.apparmor_restrict_unprivileged_userns=1
# (Ubuntu 23.10+) and Docker's default profile blocks the mount namespace. The
# profile grants only `userns,`; the container's seccomp profile and Docker
# namespaces still apply. Re-running is safe.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
SOURCE="$ROOT/deploy/opencode/kirocrew-container.apparmor"
TARGET=/etc/apparmor.d/kirocrew-container

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo: sudo $0" >&2
    exit 1
fi
if ! command -v apparmor_parser >/dev/null 2>&1; then
    echo "apparmor_parser not found; this host does not use AppArmor." >&2
    echo "The compose stack expects the profile 'kirocrew-container'." >&2
    exit 1
fi

install -m 0644 "$SOURCE" "$TARGET"
apparmor_parser -r "$TARGET"
echo "loaded AppArmor profile kirocrew-container from $TARGET"
