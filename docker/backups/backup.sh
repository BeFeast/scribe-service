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

# ---------- 1. pg_dump --------------------------------------------------------
db_out="$db_dir/scribe-$stamp.sql.gz"
log "pg_dump -> $db_out"
pg_dump --no-owner --no-privileges "$pg_url" | gzip -9 > "$db_out"
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
  page="$(curl -sf "$SCRIBE_BASE_URL/transcripts?limit=$page_limit&offset=$offset")"
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
  curl -sf "$SCRIBE_BASE_URL/transcripts/$id/summary.md"    -o "$target/summary.md"    || true
  curl -sf "$SCRIBE_BASE_URL/transcripts/$id/transcript.md" -o "$target/transcript.md" || true
  exported=$((exported + 1))
done
log "exported $exported transcripts"

# ---------- 3. prune ---------------------------------------------------------
# Cull DB dumps older than RETENTION_DAYS by mtime. The .md tree is
# small (text) and represents the current state of transcripts at backup
# time; the latest run owns every directory it touched. Stale directories
# (transcripts that vanished from the DB — should be rare) get pruned too
# when their mtime falls behind.
log "pruning entries older than $RETENTION_DAYS day(s)"
find "$db_dir" -type f -name '*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete
find "$tr_dir" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} +

log "backup OK"
printf '%s\n' "$(date -Iseconds) ok db=$db_bytes transcripts=$exported" > "$BACKUP_ROOT/_latest.log"
