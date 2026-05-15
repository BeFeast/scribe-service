#!/usr/bin/env bash
# Foreground cron loop. We re-export the runtime env into /etc/environment
# so cron child processes inherit SCRIBE_DATABASE_URL / SCRIBE_BASE_URL —
# without that step, cron jobs only see PATH=/usr/bin:/bin and trip the
# script's `set -u` check.
#
# Quoting note: the crontab sources /etc/environment via `. /etc/environment`,
# which runs under busybox ash on alpine. busybox ash does NOT understand
# bash's $'…' escape syntax that `printf %q` emits when a value contains
# backslashes or newlines, so use POSIX single-quote escaping instead.
set -euo pipefail

# POSIX-safe shell-quoting: wrap in single quotes, escape inner ones.
sq() { printf "'%s'" "$(printf %s "$1" | sed "s/'/'\\\\''/g")"; }

{
  printf 'SCRIBE_DATABASE_URL=%s\n' "$(sq "${SCRIBE_DATABASE_URL:-}")"
  printf 'SCRIBE_BASE_URL=%s\n'     "$(sq "${SCRIBE_BASE_URL:-}")"
  printf 'BACKUP_ROOT=%s\n'         "$(sq "${BACKUP_ROOT:-/backups}")"
  printf 'RETENTION_DAYS=%s\n'      "$(sq "${RETENTION_DAYS:-30}")"
  printf 'TZ=%s\n'                  "$(sq "${TZ:-UTC}")"
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
