#!/usr/bin/env sh
set -eu

echo "[entrypoint] applying alembic migrations"
uv run alembic upgrade head
echo "[entrypoint] alembic migrations applied"

exec "$@"
