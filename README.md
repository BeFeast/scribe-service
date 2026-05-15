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
| Summarise | scribe host | codex CLI (ChatGPT subscription) with a configurable prompt template |
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
├── prompts/     transcript-summary.md     ─ default summariser prompt
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

# 2. Apply migrations
docker exec scribe alembic upgrade head

# 3. Submit a job
curl -X POST http://localhost:13120/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/jNQXAC9IVRw","source":"manual"}'

# 4. Poll until done
curl http://localhost:13120/jobs/1
```

## HTTP API (excerpt)

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Submit a URL. Body: `{url, source?}`. Dedups by `video_id` against completed transcripts + in-flight jobs. |
| `GET` | `/jobs/{id}` | Job status + embedded transcript when done |
| `GET` | `/transcripts` | List, paginated |
| `GET` | `/transcripts/{id}/summary.md` | Raw summary Markdown |
| `GET` | `/transcripts/{id}/transcript.md` | Raw transcript Markdown |
| `GET` | `/healthz` | Liveness |

Web UI: `GET /` lists transcripts, `GET /transcripts/{id}` renders summary +
transcript as HTML.

## Vast.ai worker image

See `docker/vast/`. Build + push from any host with Docker and a GitHub
token that has `write:packages`.

## Migration note (2026-05-15)

Moved from `kossoy/scribe` (private, archived) to `BeFeast/scribe-service`
(public) to attach the Greptile code-review engine.

## License

Personal homelab service. No public license terms — vendor-or-fork at will.
