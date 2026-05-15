# scribe-backups

Nightly sidecar that pg_dumps the scribe DB and exports every transcript as a
flat `.md` tree, retained 30 days by default. PRD §4.12.

## What it does

Runs cron `0 3 * * *` (03:00 container TZ):

1. `pg_dump $SCRIBE_DATABASE_URL | gzip > /backups/db/scribe-<utc-stamp>.sql.gz`
2. `GET $SCRIBE_BASE_URL/transcripts` and for each id fetches `summary.md` +
   `transcript.md` into `/backups/transcripts/<id>-<slug>/`.
3. Prunes files/dirs in `/backups/{db,transcripts}/*` older than
   `$RETENTION_DAYS` by mtime.

The transcript dump is **reproducible from the DB dump** — it's a convenience
view for fast browsing / grep, not the source of truth. If you only care about
disaster recovery, you can disable the transcript step by editing the cron.

## Compose

```yaml
services:
  scribe-backups:
    image: scribe-backups:local
    build: ./src/docker/backups   # adjust to wherever you mounted this repo
    container_name: scribe-backups
    restart: unless-stopped
    env_file: .env                # reuse scribe's .env for SCRIBE_DATABASE_URL
    environment:
      SCRIBE_BASE_URL: http://scribe:8000   # talk to the scribe service container
      TZ: Europe/Tel_Aviv                   # or your local
      RETENTION_DAYS: "30"
    volumes:
      - /var/backups/scribe:/backups        # bind to a TrueNAS-backed host path
    depends_on:
      scribe:
        condition: service_healthy
```

If scribe runs on the same compose network, `SCRIBE_BASE_URL=http://scribe:8000`
just works. From another network, point it at the published port
(`http://10.10.0.13:13120`).

## Manual run

```bash
docker compose run --rm scribe-backups run-now
```

(Skips cron, runs `scribe-backup` once, exits.)

## Restoring

```bash
gunzip < /var/backups/scribe/db/scribe-<stamp>.sql.gz \
  | docker exec -i db-dev-postgres psql -U scribe -d scribe
```

(Drop + recreate the database first if you want a clean slate.)

## Outputs

```
/backups/
├── db/
│   ├── scribe-20260515-030000.sql.gz
│   └── scribe-20260516-030000.sql.gz
├── transcripts/
│   ├── 1-me-at-the-zoo/
│   │   ├── summary.md
│   │   └── transcript.md
│   └── ...
├── _latest.log         single line: "<iso> ok db=<bytes> transcripts=<count>"
├── _last_success_ts    epoch seconds of the last successful run; consumed
│                       by scribe's GET /admin/backup-status
└── _cron.log           appended on every cron run
```

## Healthcheck

After each successful run `backup.sh` writes `/backups/_last_success_ts` (epoch
seconds). Mount the same volume read-only into the scribe container and curl
`GET /admin/backup-status`:

```bash
curl -fs http://scribe:8000/admin/backup-status
# {"path":"/backups/_last_success_ts","last_success_ts":1747000800,
#  "last_success_iso":"2026-05-15T03:00:00+00:00","age_seconds":3600,
#  "stale_after_seconds":90000,"stale":false}
```

The endpoint returns 200 unconditionally; alert on the `stale` flag (true when
the heartbeat is missing, unreadable, or older than
`SCRIBE_BACKUP_STALE_AFTER_SECONDS`, default ~25h).
