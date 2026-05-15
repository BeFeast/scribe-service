#!/usr/bin/env bash
# Foreground cron loop. We re-export the runtime env into /etc/environment
# so cron child processes inherit SCRIBE_DATABASE_URL / SCRIBE_BASE_URL —
# without that step, cron jobs only see PATH=/usr/bin:/bin and trip the
# script's `set -u` check.
set -euo pipefail

# Snapshot env that backup.sh needs.
{
  printf 'SCRIBE_DATABASE_URL=%q\n' "${SCRIBE_DATABASE_URL:-}"
  printf 'SCRIBE_BASE_URL=%q\n'     "${SCRIBE_BASE_URL:-}"
  printf 'BACKUP_ROOT=%q\n'         "${BACKUP_ROOT:-/backups}"
  printf 'RETENTION_DAYS=%q\n'      "${RETENTION_DAYS:-30}"
  printf 'TZ=%q\n'                  "${TZ:-UTC}"
} > /etc/environment

mkdir -p "${BACKUP_ROOT:-/backups}"

echo "[entrypoint] scribe-backup ready, cron schedule:"
cat /etc/cron.d/scribe-backup

# RUN-NOW mode: one-shot for ad-hoc invocation (`docker compose run --rm
# scribe-backups run-now`). Skips cron entirely.
if [[ "${1:-}" == "run-now" ]]; then
  exec /usr/local/bin/scribe-backup
fi

# Otherwise, foreground cron.
exec crond -f -d 8
