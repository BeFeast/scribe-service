# Vast.ai whisper worker image

GPU transcription container for `scribe`. Pre-cached `large-v3-turbo`,
trimmed cuDNN, ffmpeg + `uv` + faster-whisper — no yt-dlp/deno/git/unzip
(those run off-Vast on the residential-IP scribe host).

Pushed to `ghcr.io/befeast/scribe-service-vast:cuda12.4-whisper`. The legacy
`ghcr.io/kossoy/openclaw-video-summary-vast:cuda12.4-fast` image still exists
for the rollback path; do not retire it until rollback is no longer needed.

## Build & push (from workshop)

```bash
cd docker/vast
docker build -t ghcr.io/befeast/scribe-service-vast:cuda12.4-whisper .
docker push ghcr.io/befeast/scribe-service-vast:cuda12.4-whisper
```

`docker login ghcr.io` needs a token with `write:packages`. If the local
`gh` token doesn't have it:

```bash
gh auth refresh -h github.com -s write:packages,read:packages
gh auth token | docker login ghcr.io -u <github-user> --password-stdin
```

## What changed vs the old `cuda12.4-fast`

- dropped `yt-dlp` (download moved off-Vast)
- dropped `deno` (no EJS player JS extraction on Vast)
- dropped `unzip`, `git`
- kept `uv` (venv build + onstart fallback if image is stale on a fresh host)
- kept `ffmpeg` (faster-whisper decodes incoming WAV through it on the av-less path)
- venv has only `faster-whisper` (no `yt-dlp`, no `requests`)

Result: ~9.9 GB final, fast cold-start, single responsibility.
