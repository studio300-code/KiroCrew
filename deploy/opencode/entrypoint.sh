#!/bin/sh
# KiroCrew + OpenCode container entrypoint.
#
# 1. Seed the clean, first-run user configuration and OpenCode auth.
# 2. Move runtime credentials into the gateway's .env and remove them from the
#    long-lived process environment.
# 3. Hand over to tini + kirocrew.

set -eu

if ! python3 /usr/local/bin/kirocrew-opencode-seed; then
    echo "[entrypoint] WARNING: OpenCode seed failed; continuing with the existing configuration." >&2
fi

ACTIVE_HOME=$(python3 -c 'from kiro_crew.config.paths import ensure_data_home; print(ensure_data_home())' 2>/dev/null || printf '%s' "${HOME:-/home/kirocrew}/.kiro/crew")
ENV_FILE="$ACTIVE_HOME/.env"
mkdir -p "$ACTIVE_HOME"

# The seed writes OPENROUTER_API_KEY itself. These are the channel/kiro
# credentials the stock Docker image also scrubs. Keep the list to what this
# appliance exposes; every value is replaced line-wise so other secrets survive.
for KEY in \
    SLACK_BOT_TOKEN SLACK_APP_TOKEN KIROCREW_OWNER_ID \
    DISCORD_BOT_TOKEN TELEGRAM_BOT_TOKEN \
    WECOM_BOT_ID WECOM_SECRET WEBEX_BOT_TOKEN WEIXIN_TOKEN \
    FEISHU_APP_ID FEISHU_APP_SECRET \
    MICROSOFT_APP_ID MICROSOFT_APP_PASSWORD MICROSOFT_APP_TENANT_ID \
    JIRA_API_TOKEN AZURE_DEVOPS_EXT_PAT BITBUCKET_EMAIL BITBUCKET_API_TOKEN \
    KIRO_API_KEY; do
    VAL=$(eval "printf '%s' \"\${$KEY:-}\"")
    [ -n "$VAL" ] || continue
    TMP="$ENV_FILE.tmp.$$"
    if [ -f "$ENV_FILE" ]; then
        grep -v "^$KEY=" "$ENV_FILE" > "$TMP" 2>/dev/null || true
    else
        : > "$TMP"
    fi
    printf '%s=%s\n' "$KEY" "$VAL" >> "$TMP"
    chmod 600 "$TMP"
    mv "$TMP" "$ENV_FILE"
    unset "$KEY"
    echo "[entrypoint] Stored $KEY in $ENV_FILE and removed it from the environment."
done

# Tell the gateway credentials were deliberately scrubbed; it must load .env
# and must not re-inject the process environment.
export _KIROCREW_CREDS_SCRUBBED=1

if [ "${1:-gateway}" = "gateway" ]; then
    PORT="${KIROCREW_PORT:-5476}"
    echo "[entrypoint] Dashboard login: run  docker exec <container> kirocrew token --ttl 2h"
    echo "[entrypoint] then open the printed link, replacing the host with how you reach"
    echo "[entrypoint] this container (e.g. http://localhost:${PORT}/?token=... with -p ${PORT}:${PORT})."
fi

if [ $# -eq 0 ]; then
    set -- gateway
fi

if [ "$1" = "gateway" ]; then
    shift
    exec tini -- kirocrew gateway --no-open "$@"
fi
exec tini -- kirocrew "$@"
