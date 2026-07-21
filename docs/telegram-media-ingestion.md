# Telegram media ingestion contract (#417)

Scribe accepts YouTube/other URLs (yt-dlp), direct media URLs, and user uploads
(#408). This document defines a fourth ingestion path: **opaque Telegram media
references**, for media too large for a consumer bot's inline download path.

## Why

A Telegram bot's inline `getFile` download is limited to 20 MB against the
public Bot API. When a user sends a larger video/audio, the integration only
holds a Telegram **`file_id`** — an opaque, bot-scoped handle, not a URL Scribe
can hand to yt-dlp. Previously such a submission failed before it could create a
usable job. This path lets that reference be submitted and resolved.

## What is accepted

The Telegram integration submits a normal `POST /jobs` request whose `url` is:

```
tg:<file_id>
```

* `<file_id>` is a URL-safe token (`A–Z a–z 0–9 _ -`, 8–512 chars). It is
  **opaque** and **carries no secret** — no bot token, no session string, no
  signed URL. Anything else is rejected as `telegram_unsupported`.
* The reference is keyed to a stable `telegram:<sha256-digest>` `video_id` at
  submit time, so submit-time dedup works exactly like a YouTube id and the raw
  `file_id` never lands verbatim in a `video_id` column, log, or URL.
* Existing YouTube, direct-media-URL, and upload behavior is unchanged: only a
  `tg:` prefix routes to this path.

## What expires

`file_id` values are **not permanent**. They are bot-scoped and can expire or
become invalid. An expired / invalid / inaccessible reference surfaces as a
typed, user-facing error (`telegram_expired`), asking the user to re-send the
media. This is expected, not a bug — the reference is a handle, not storage.

## Which component may resolve the reference

**Only the Scribe worker's Telegram adapter** (`scribe.pipeline.telegram`) is
allowed to resolve a `tg:` reference. Resolution:

1. Calls the Telegram Bot API `getFile` to turn the `file_id` into a
   `file_path`, using a bot token held **only in server config**
   (`SCRIBE_TELEGRAM_BOT_TOKEN`).
2. Reads the media bytes — either streamed from the Bot API `file/` endpoint, or
   (with a self-hosted `telegram-bot-api --local` server) read directly from the
   on-disk path `getFile` returns, with no HTTP transfer.
3. Presents a `DownloadResult` so the unchanged ffmpeg → whisper → summary
   pipeline processes it like any other source.

### Download size ceiling

| Bot API base URL                      | Max size |
| ------------------------------------- | -------- |
| `https://api.telegram.org` (default)  | 20 MB    |
| self-hosted `telegram-bot-api`        | 2 GB     |
| self-hosted `telegram-bot-api --local`| 2 GB, no HTTP transfer (direct file read) |

`SCRIBE_TELEGRAM_MAX_BYTES` (default 2 GB) is a defence-in-depth ceiling: an
oversize `file_size` from `getFile` is rejected before the download starts, and
the byte count is re-checked mid-stream so an oversize transfer is aborted.

## Secret handling

* The bot token lives only in server config (Infisical /
  `SCRIBE_TELEGRAM_BOT_TOKEN`). It is registered in
  `scribe.obs.logging._SECRET_SETTING_FIELDS`, so its value is scrubbed from
  every log line.
* The token-bearing `getFile` / `file/` request URLs are **never logged** and
  never appear in a job record, API payload, or error message.
* `TelegramRefError` messages are user-facing and secret-free by construction.
* The submitted `tg:<file_id>` reference carries no credential, so echoing it
  back in the job record / API payload exposes no secret.

## Configuration

| Setting                          | Env var                          | Default                     |
| -------------------------------- | -------------------------------- | --------------------------- |
| `telegram_bot_token`             | `SCRIBE_TELEGRAM_BOT_TOKEN`      | `""` (path disabled)        |
| `telegram_api_base_url`          | `SCRIBE_TELEGRAM_API_BASE_URL`   | `https://api.telegram.org`  |
| `telegram_download_timeout_s`    | `SCRIBE_TELEGRAM_DOWNLOAD_TIMEOUT_S` | `600`                   |
| `telegram_max_bytes`             | `SCRIBE_TELEGRAM_MAX_BYTES`      | `2147483648` (2 GB)         |

When `telegram_bot_token` is empty the path is disabled: a `tg:` submission
still creates a job, but the worker fails it with an actionable
`telegram_not_configured` error.

## Error taxonomy

All failures raise `TelegramRefError` with a stable `reason` and a secret-free,
user-facing message. The worker surfaces the message in `job.error`.

| `reason`                    | Meaning / user action                                   |
| --------------------------- | ------------------------------------------------------- |
| `telegram_unsupported`      | Not a well-formed `tg:<file_id>` reference.             |
| `telegram_not_configured`   | Server has no bot token; ingestion path disabled.       |
| `telegram_expired`          | Reference expired/invalid/gone — re-send the media.     |
| `telegram_too_large`        | Exceeds the download ceiling; needs a local Bot API.    |
| `telegram_inaccessible`     | Network/API failure reaching Telegram — retry shortly.  |

Refs #416.
