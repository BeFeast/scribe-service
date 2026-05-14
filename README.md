# scribe

Self-hosted YouTube video-summary service: **URL → transcript → summary**, with its
own API, Postgres-backed history, and a web-UI to browse past transcripts.

**Obsidian-agnostic by design** — scribe knows nothing about Obsidian or Telegram.
Consumers (the shtrudel OpenClaw skill) handle delivery and Obsidian writes.

## Why

The previous pipeline ran `yt-dlp` on Vast.ai datacenter IPs, which YouTube
intermittently bot-walls (`Sign in to confirm you're not a bot`). scribe runs the
download from a home residential IP — the bot-wall structurally cannot trigger.
Vast.ai is kept, but only for GPU whisper transcription.

Full design + Phase-1 plan: `HomeLab/Projects/video-summary-service-design-2026-05-14.md`
in the Obsidian vault.

## Pipeline

```
URL → yt-dlp (residential IP) → ffmpeg 16k mono → Vast whisper (GPU) → summary (codex CLI) → Postgres
```

## Layout

```
src/scribe/
  api/        HTTP API
  web/        browse UI (Jinja)
  worker/     job queue loop
  pipeline/   downloader · ffmpeg · whisper_client · summarizer · shortlinks
  db/         SQLAlchemy models + session
```

## Status

Phase 1 (MVP) — scaffolding. Not yet runnable end-to-end.
