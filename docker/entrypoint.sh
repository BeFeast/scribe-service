#!/usr/bin/env sh
# Container entrypoint. Sources Infisical Agent-rendered SCRIBE_* secrets
# from a shared volume before launching uvicorn, runs alembic migrations,
# then exec()'s the CMD. Fail-loud guard refuses to boot with the loopback-
# only trusted_cidrs default (issue #261), so a cold start with no env-file
# and no env-injected secrets exits non-zero and lets docker restart us
# instead of silently 401-ing every LAN client.
set -eu

INFISICAL_ENV_FILE="${SCRIBE_INFISICAL_ENV_FILE:-/secrets/scribe.env}"
if [ -f "$INFISICAL_ENV_FILE" ] && [ -s "$INFISICAL_ENV_FILE" ]; then
    echo "[entrypoint] sourcing $INFISICAL_ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    . "$INFISICAL_ENV_FILE"
    set +a
else
    echo "[entrypoint] no env-file at $INFISICAL_ENV_FILE; relying on container env"
fi

if [ "${SCRIBE_BOOT_REQUIRE_SECRETS:-1}" = "1" ]; then
    missing=""
    [ -z "${SCRIBE_TRUSTED_CIDRS:-}" ] && missing="$missing SCRIBE_TRUSTED_CIDRS"
    [ -z "${SCRIBE_MACHINE_BEARER_TOKEN:-}" ] && missing="$missing SCRIBE_MACHINE_BEARER_TOKEN"
    if [ -n "$missing" ]; then
        echo "[entrypoint] FATAL: missing required boot secrets:$missing" >&2
        echo "[entrypoint] refusing to start with loopback-only trust (see #261)" >&2
        exit 1
    fi
fi

echo "[entrypoint] applying alembic migrations"
uv run alembic upgrade head
echo "[entrypoint] alembic migrations applied"

exec "$@"
