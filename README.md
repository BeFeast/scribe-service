# scribe-service

Self-hosted YouTube video-summary service. Submit a URL, get back a transcript +
a Markdown summary, browse history through an HTTP API or a server-rendered web
UI. Designed to be **delivery-agnostic** — consumers (a Telegram bot, an Obsidian
plugin, a CLI) handle whatever happens with the result.

```
┌─────────┐  POST /jobs   ┌──────────┐
│ client  │ ─────────────▶│  scribe  │
└─────────┘  GET /jobs/id └─────┬────┘
                                │
            yt-dlp (residential IP) ─┐
            ffmpeg 16k mono ─────────┤
            Vast.ai whisper (GPU)  ──┼─▶ Postgres
            codex CLI summary  ──────┤
            Chhoto shortlinks  ──────┘
```

The original problem: `yt-dlp` on Vast.ai datacenter IPs trips YouTube's
intermittent bot-wall (`Sign in to confirm you're not a bot`). The bot-wall
is per-IP + per-client + at the `player` stage upstream of where PO tokens
apply — structurally unfixable without a residential IP. scribe runs the
download from a residential IP (a homelab box behind a regular ISP) and
keeps Vast.ai purely for whisper GPU transcription.

## Pipeline

| Stage | Where | Notes |
|---|---|---|
| Download | scribe host (LAN/residential) | yt-dlp + EJS via deno + client-fallback chain + bot-wall retry |
| Normalise audio | scribe host | ffmpeg → 16 kHz mono WAV |
| Transcribe | Vast.ai GPU instance | `faster-whisper large-v3-turbo` (float16, CUDA). See `docker/vast/` |
| Summarise | scribe host | codex CLI (ChatGPT subscription) with versioned prompt templates |
| Shortlinks | scribe host → Chhoto | Public `go.oklabs.uk/<slug>` for both summary + transcript |
| Persist | Postgres | `Job` (queue) + `Transcript` (results) tables |

## Repo layout

```
src/scribe/
├── api/         routes.py, schemas.py     ─ FastAPI routes
├── db/          models.py, session.py     ─ SQLAlchemy 2.0
├── pipeline/    downloader, ffmpeg, whisper_client, summarizer, shortlinks
├── web/         views.py, templates/      ─ Jinja list + detail
├── worker/      loop.py                   ─ Postgres-backed queue worker (FOR UPDATE SKIP LOCKED)
├── prompts/     transcript-summary.v*.md  ─ versioned summariser prompts
├── config.py    pydantic-settings
└── main.py      FastAPI app + lifespan-started worker threads

docker/
└── vast/        Dockerfile + README       ─ whisper-only GPU image

migrations/      alembic                   ─ schema migrations
Dockerfile       service container
compose.yaml     reference deployment (env_file: .env, codex bind-mount, named-volume tmp)
```

## Quick start

```bash
# 1. Postgres + .env (DATABASE_URL, VAST_API_KEY, SHORTLINK_* env vars, ...)
cp .env.example .env  # edit values
docker compose up -d --build

# 2. Submit a job
curl -X POST http://localhost:13120/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/jNQXAC9IVRw","source":"manual"}'

# 3. Poll until done
curl http://localhost:13120/jobs/1
```

The service image runs `uv run alembic upgrade head` in its entrypoint before
starting Uvicorn. If migrations fail, the container exits instead of serving
traffic against a stale schema.

## Operational logs

The `scribe` compose service writes container stdout/stderr to journald with
the Docker tag `scribe`, so logs survive `docker compose up --build scribe`
replacing the container. To inspect logs from before the most recent redeploy
on the devbox:

```bash
journalctl --since "7 days ago" CONTAINER_TAG=scribe
```

Narrow the time window when investigating an incident, for example:

```bash
journalctl --since "2026-05-24 00:00" --until "2026-05-27 12:00" CONTAINER_TAG=scribe
```

Retention check on the devbox, verified 2026-05-27: journald uses persistent
storage under `/var/log/journal`, the visible journal spans 2026-04-23 through
2026-05-27, and journal disk usage is about 425 MB on a 129 GB root filesystem.
That is more than seven days of local journal capacity for scribe output.

## HTTP API (excerpt)

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Submit a URL. Body: `{url, source?}`. Dedups by `video_id` against completed transcripts + in-flight jobs. |
| `GET` | `/jobs/{id}` | Job status + embedded transcript when done |
| `GET` | `/transcripts` | List, paginated |
| `GET` | `/transcripts/{id}/summary.md` | Raw summary Markdown |
| `GET` | `/transcripts/{id}/transcript.md` | Raw transcript Markdown |
| `GET` | `/api/prompts` | List summarizer prompt versions and the active version |
| `GET` | `/api/prompts/{version}` | Raw prompt Markdown for `v1`, `v2`, or `v3` |
| `POST` | `/api/prompts/{version}` | Atomically replace a prompt version body |
| `POST` | `/api/prompts/active` | Switch the active prompt version |
| `POST` | `/api/prompts/dry-run` | Re-summarize an existing transcript with a chosen prompt without persisting |
| `POST` | `/api/transcripts/{id}/share-links` | Create a revocable share link. Body: `{target_kind, expires_at?, label?, recipient_note?}`; `target_kind` is `page`, `summary_markdown`, `transcript_markdown` or `ai_transcript`. Owner or admin only. |
| `GET` | `/api/transcripts/{id}/share-links` | List the transcript's share links, with `access_count` and `last_accessed_at` |
| `POST` | `/api/share-links/{id}/revoke` | Revoke a share link; its URL answers `410` from then on |
| `GET`, `HEAD` | `/share/{token}[.md\|.txt][?part=N]` | Public, no login. Suffixes and `?part` apply to `ai_transcript` links only |
| `GET` | `/healthz` | Liveness |

Web UI: `GET /` lists transcripts, `GET /transcripts/{id}` renders summary +
transcript as HTML.

### Copy for AI / agent access

An `ai_transcript` share link serves the raw transcript to a web AI (ChatGPT,
Claude, Gemini) or an agent, without a login and without a summary: it also
works for partial transcripts (`summary_md IS NULL`). The Share sheet's
"Copy for AI" action creates one with a 7-day, 30-day (default) or no expiry
and lists the active links with a Revoke button.

- **Format:** `Key: value` header lines (`Title`, `Source`, `Author`,
  `Platform`, `Duration`, `Language`, `Transcribed`, `Length`, `Timestamps`,
  `Note`; empty fields are left out), an empty line, `---`, an empty line, then
  the transcript text. The stored `## Metadata` block is not served. The raw
  source URL of an upload is never printed (`Source: uploaded file`), and
  neither is a Telegram private-chat URL.
- **Content type:** `/share/{token}` and `/share/{token}.txt` return
  `text/plain; charset=utf-8`; `/share/{token}.md` returns
  `text/markdown; charset=utf-8`. The body is the same. `HEAD` is answered too.
- **Headers on every `/share/*` response, errors included:**
  `X-Robots-Tag: noindex, nofollow, noarchive`, `Cache-Control: no-store`,
  `Access-Control-Allow-Origin: *` (no credentials),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
  `robots.txt` does not block `/share/`, because user-initiated AI fetchers
  honour it.
- **Long transcripts:** the full text is always served without `?part`. Above
  100,000 characters the header adds `Parts available: M` and one URL per part;
  `?part=N` (1-based) serves a chunk of at most 100,000 characters, cut at a
  sentence end or whitespace, with `Part: N of M` and the next part's URL.
  An out-of-range part returns `404`.
- **Status codes:** unknown token `404`, revoked or expired `410`, suffix on a
  non-AI link `404`. Each successful `GET` increments `access_count` and sets
  `last_accessed_at`.

An agent with the machine bearer token creates a link and fetches it like this
(`share_url_md` is `share_url` plus `.md`):

```bash
curl -s -X POST https://scribe.example/api/transcripts/123/share-links \
  -H "Authorization: Bearer $SCRIBE_MACHINE_TOKEN" -H "Content-Type: application/json" \
  -d '{"target_kind":"ai_transcript","expires_at":"2026-12-31T00:00:00Z","label":"agent:my-bot"}' | jq -r .share_url_md
curl -s "https://scribe.example/share/<token>.md"          # full text
curl -s "https://scribe.example/share/<token>.md?part=2"   # chunk N for long transcripts
```

## Production acceptance

Run from Scribe's trusted LAN with Bun, Node.js and an existing Chrome DevTools
Protocol (CDP) endpoint. Set `SCRIBE_VERIFY_CDP_URL` to the endpoint's HTTP
origin. Production must have the acceptance job API and its schema migration
deployed. Then run:

```bash
bun install --cwd scripts --frozen-lockfile
scripts/verify all
```

Run `scripts/verify <scenario>` for one scenario from `acceptance/`.
`copy-for-ai-share` creates its own fixture when run on its own and reuses the
same transcription during `all`. The extension scenario reports `MANUAL` with
the setup it needs. Automated runs submit an isolated production job. Cleanup
verifies ownership of its transcript and share before deleting them. The runner
prints the directory containing screenshots and `result.json`.
Set `TMPDIR` to choose where `mktemp` creates that directory.

## Auth v2

Scribe keeps product authorization in Postgres. Clerk signs humans in; Scribe
verifies Clerk JWTs with the configured JWKS, maps the Clerk subject/email to a
local `users` row, and scopes jobs/transcripts by `owner_id`.
If the Clerk session token does not include an email claim, Scribe uses the
Clerk Backend API and `SCRIBE_CLERK_SECRET_KEY` to resolve the user profile by
Clerk subject. After a subject is linked to a local user, future session tokens
do not need to carry email on every request.

Configure:

```bash
SCRIBE_AUTH_CLERK_ISSUER=https://your-clerk-domain
SCRIBE_AUTH_CLERK_JWKS_URL=https://your-clerk-domain/.well-known/jwks.json
SCRIBE_CLERK_BACKEND_API_URL=https://api.clerk.com
SCRIBE_CLERK_SECRET_KEY=...
SCRIBE_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
SCRIBE_MACHINE_BEARER_TOKEN=... # automation fallback only
```

The bootstrap admin email is only used when the first Clerk user signs in and
the `users` table is empty. After that, admins manage allowed users through:

```bash
curl -H "Authorization: Bearer <admin Clerk session token>" \
  -X POST http://localhost:13120/api/admin/users \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","role":"user"}'
```

Trusted LAN clients remain passwordless. External clients must use a Clerk
session token, a generated extension token, or the machine bearer token. To set
up the Chrome extension without fishing secrets from Infisical, sign in to the
Scribe UI, create an extension token with:

```bash
curl -H "Authorization: Bearer <admin Clerk session token>" \
  -X POST http://localhost:13120/api/auth/extension-token \
  -H "Content-Type: application/json" \
  -d '{"label":"Chrome extension"}'
```

Store the returned `stx_...` token in the extension and send it as
`Authorization: Bearer stx_...` on `POST /jobs`. The raw public transcript and
summary links under `/transcripts/{id}` remain public.

## Secret delivery (Infisical Agent sidecar)

`scribe` reads its boot-time secrets (`SCRIBE_TRUSTED_CIDRS`,
`SCRIBE_MACHINE_BEARER_TOKEN`, etc.) from env vars rendered into a
shared volume by an Infisical Agent sidecar. The container entrypoint
sources `/secrets/scribe.env` before launching uvicorn, and refuses to
start if those required secrets are missing — this prevents the boot-
time race that previously left scribe serving with the loopback-only
`trusted_cidrs` default and 401-ing every LAN client. See
[`docs/runtime/infisical-agent.md`](docs/runtime/infisical-agent.md)
for the compose snippet and the fail-mode matrix.

## Vast.ai worker image

See `docker/vast/`. Build + push from any host with Docker and a GitHub
token that has `write:packages`.

## Migration note (2026-05-15)

Moved from `kossoy/scribe` (private, archived) to `BeFeast/scribe-service`
(public) to attach the Greptile code-review engine.

## Releases

scribe-service uses [SemVer](https://semver.org/) (`MAJOR.MINOR.PATCH`) with a
label-driven, per-merge release pipeline.

- **Single source of truth:** the `version` field in `pyproject.toml`. Nothing
  else carries the canonical version.
- **Per-PR label:** every pull request carries exactly one `semver:*` label that
  declares the bump it should produce when merged:

  | Label | Bump | When |
  |---|---|---|
  | `semver:major` | `MAJOR` | Breaking API / behaviour change |
  | `semver:minor` | `MINOR` | Backward-compatible feature |
  | `semver:patch` | `PATCH` | Fix, docs, chore, refactor |

- **Default is `patch`:** an unlabeled PR is treated as `semver:patch`.
- **Cadence is per-merge, continuous:** every merge to `main` triggers a release.
  The pipeline bumps the `pyproject.toml` version according to the merged PR's
  label, creates a `vX.Y.Z` git tag, and publishes the corresponding release.
  There is no separate manual release step or batched release train — one merge,
  one version bump, one tag, one release.

Every release gets a dated `vX.Y.Z` section in [`CHANGELOG.md`](CHANGELOG.md),
listing the merged pull-request titles in the tag range. Cut one with:

```bash
uv run python scripts/generate_changelog.py vX.Y.Z --bump patch
```

This prepends the new section (newest first) from `git log <previous-tag>..HEAD`
and is idempotent — re-running for an already-recorded version is a no-op. Pass
`--from <tag>`, `--date YYYY-MM-DD`, or `--dry-run` to override the defaults.

## License

Personal homelab service. No public license terms — vendor-or-fork at will.
