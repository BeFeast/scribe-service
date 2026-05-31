#!/usr/bin/env bash
# scribe-backup — invoked by cron at 03:00.
#
# Required env:
#   SCRIBE_DATABASE_URL     postgres URL (libpq form OR sqlalchemy form)
#   SCRIBE_BASE_URL         scribe HTTP base, e.g. http://scribe:8000
# Optional:
#   BACKUP_ROOT             default /backups
#   RETENTION_DAYS          default 30
#
# Writes:
#   $BACKUP_ROOT/db/scribe-YYYYMMDD-HHMMSS.sql.gz
#   $BACKUP_ROOT/transcripts/<id>-<slug>/{summary,transcript}.md
#   $BACKUP_ROOT/_latest.log
set -euo pipefail

: "${SCRIBE_DATABASE_URL:?SCRIBE_DATABASE_URL not set}"
: "${SCRIBE_BASE_URL:?SCRIBE_BASE_URL not set}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*"; }

# pg_dump understands libpq URLs. SQLAlchemy prefixes the scheme with
# `postgresql+psycopg://` — strip it so pg_dump accepts it too.
pg_url="${SCRIBE_DATABASE_URL/postgresql+psycopg:/postgresql:}"

stamp="$(date -u +%Y%m%d-%H%M%S)"
db_dir="$BACKUP_ROOT/db"
tr_dir="$BACKUP_ROOT/transcripts"
mkdir -p "$db_dir" "$tr_dir"

# Single-run guard. A duplicated cron source (and, more rarely, an ad-hoc
# `run-now` overlapping the nightly job) could start two backups in the
# same second; they share the per-second $stamp and raced on the same
# .sql.gz.tmp, so one mv failed under `set -e` before the success
# heartbeat was written — leaving ops permanently "stale". Atomic mkdir
# lock, with a staleness escape so a crashed run cannot wedge backups.
lock="$BACKUP_ROOT/.backup.lock"
if ! mkdir "$lock" 2>/dev/null; then
  if find "$lock" -maxdepth 0 -mmin +120 >/dev/null 2>&1; then
    log "stale lock $lock (>120m) — stealing"
    rmdir "$lock" 2>/dev/null || true
    mkdir "$lock" 2>/dev/null || { log "could not acquire $lock; exiting"; exit 0; }
  else
    log "another scribe-backup run holds $lock; exiting"
    exit 0
  fi
fi
trap 'rmdir "$lock" 2>/dev/null || true' EXIT

# ---------- 1. pg_dump --------------------------------------------------------
# Write to a hidden .tmp first and atomically rename on success. On any failure
# the .tmp is cleaned up, so retention never sees a partial / unrestorable file
# with a valid-looking name.
db_tmp="$db_dir/.scribe-$stamp.sql.gz.tmp"
db_out="$db_dir/scribe-$stamp.sql.gz"
log "pg_dump -> $db_out"
if pg_dump --no-owner --no-privileges "$pg_url" | gzip -9 > "$db_tmp"; then
  mv "$db_tmp" "$db_out"
else
  rm -f "$db_tmp"
  log "pg_dump FAILED (db_url ok? server reachable? version match?)"
  exit 1
fi
db_bytes="$(stat -c%s "$db_out")"
log "pg_dump done ($db_bytes bytes)"

# ---------- 2. transcript .md tree -------------------------------------------
# Each id becomes <id>-<slug>/{summary,transcript}.md. Re-runs are idempotent
# (overwrite). Partial transcripts (summary_md NULL) are skipped — scribe's
# /transcripts already hides them by default.
log "exporting transcripts as .md tree"
exported=0
# scribe caps /transcripts limit at 200; page until a short page comes back.
page_limit=200
offset=0
rows=()
while :; do
  page="$(curl -sf "$SCRIBE_BASE_URL/transcripts?limit=$page_limit&offset=$offset" || true)"
  if [[ -z "$page" ]]; then
    log "warn: transcript list fetch failed (HTTP error / auth?). DB dump is complete; skipping .md export."
    break
  fi
  count=$(printf '%s' "$page" | jq 'length')
  if [[ "$count" -eq 0 ]]; then
    break
  fi
  while IFS= read -r r; do rows+=("$r"); done < <(
    printf '%s' "$page" | jq -r '.[] | "\(.id)\t\(.title)"'
  )
  [[ "$count" -lt "$page_limit" ]] && break
  offset=$((offset + page_limit))
done
for row in "${rows[@]}"; do
  id="${row%%	*}"
  title="${row#*	}"
  # slug — lowercase, alnum-only, single dashes, max 80 chars
  slug="$(printf '%s' "$title" | tr '[:upper:]' '[:lower:]' \
           | sed -E 's/[^a-z0-9а-яё]+/-/g; s/^-+|-+$//g' \
           | cut -c1-80)"
  [[ -z "$slug" ]] && slug="transcript"
  target="$tr_dir/$id-$slug"
  mkdir -p "$target"
  wrote_summary=0; wrote_transcript=0
  curl -sf "$SCRIBE_BASE_URL/transcripts/$id/summary.md"    -o "$target/summary.md"    && wrote_summary=1    || true
  curl -sf "$SCRIBE_BASE_URL/transcripts/$id/transcript.md" -o "$target/transcript.md" && wrote_transcript=1 || true
  if (( wrote_summary || wrote_transcript )); then
    # Touch the dir so the retention prune sees a fresh mtime even when both
    # markdown files are identical to the previous run (overwrite via curl -o
    # preserves the dir mtime on most filesystems).
    touch "$target"
    exported=$((exported + 1))
  else
    log "warn: id=$id had no readable artifacts; leaving dir untouched"
  fi
done
log "exported $exported transcripts"

# ---------- 3. prune ---------------------------------------------------------
# DB dumps prune purely by filename mtime (each run is a fresh file). For the
# .md tree we look at the newest file inside each subdir — `mkdir -p` on an
# existing directory doesn't refresh its mtime, and overwriting the same
# `summary.md` doesn't either, so dir-mtime would falsely report long-lived
# transcripts as stale. Newest-inner-file mtime is the right signal.
log "pruning entries older than $RETENTION_DAYS day(s)"
find "$db_dir" -type f -name '*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete || log "warn: db prune had errors (continuing)"
while IFS= read -r -d '' d; do
  newest=$(find "$d" -type f -printf '%T@\n' 2>/dev/null | sort -nr | head -1 || true)  # head closes the pipe early; tolerate SIGPIPE (141) under set -o pipefail
  if [[ -n "$newest" ]]; then
    age_days=$(awk -v n="$newest" 'BEGIN { print int((systime() - n) / 86400) }')
    if (( age_days > RETENTION_DAYS )); then
      echo "$d"
      rm -rf "$d"
    fi
  fi
done < <(find "$tr_dir" -mindepth 1 -maxdepth 1 -type d -print0)

log "backup OK"
printf '%s\n' "$(date -Iseconds) ok db=$db_bytes transcripts=$exported" > "$BACKUP_ROOT/_latest.log"

# Last-success heartbeat consumed by scribe's GET /admin/backup-status. Write
# via a sibling .tmp + rename so a concurrent reader never sees a half-written
# value (epoch ints are short but a slow disk + truncate can still split).
ts_tmp="$BACKUP_ROOT/.tmp.scribe-last-success-ts.$$"
date +%s > "$ts_tmp"
mv "$ts_tmp" "$BACKUP_ROOT/_last_success_ts"
