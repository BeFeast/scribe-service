# Nightly backup sidecar for scribe.
#
# Runs a cron loop (03:00 daily) that:
#   1. pg_dumps the scribe DB to /backups/db/scribe-<date>.sql.gz
#   2. exports every done Transcript to /backups/transcripts/<id>-<slug>/{summary,transcript}.md
#   3. prunes anything in /backups older than $RETENTION_DAYS (default 30)
#
# Reads SCRIBE_DATABASE_URL + SCRIBE_BASE_URL from env. Keep this image
# tiny: postgres:18-alpine already has pg_dump 18 + libc + tzdata; add
# only the few extras we need (cron, bash, jq, curl, tini, findutils).

FROM postgres:18-alpine

ENV TZ=UTC \
    RETENTION_DAYS=30 \
    BACKUP_ROOT=/backups

RUN apk add --no-cache \
      bash \
      ca-certificates \
      coreutils \
      curl \
      dcron \
      findutils \
      jq \
      tini \
      tzdata

COPY backup.sh /usr/local/bin/scribe-backup
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY crontab /etc/cron.d/scribe-backup

RUN chmod +x /usr/local/bin/scribe-backup /usr/local/bin/entrypoint.sh \
    && chmod 600 /etc/cron.d/scribe-backup \
    && crontab /etc/cron.d/scribe-backup

# The postgres:18-alpine base sets USER postgres for its own entrypoint —
# we don't want that; cron + write to /backups need root inside this image.
USER root

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
