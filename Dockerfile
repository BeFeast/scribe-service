# syntax=docker/dockerfile:1.6
# scribe — YouTube download + ffmpeg + codex summary + HTTP API/web UI.
# Vast GPU whisper transcription happens out-of-process; this image only
# needs the local-pipeline tools (yt-dlp, ffmpeg, deno for EJS, codex CLI)
# plus the scribe Python package.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:/root/.deno/bin:${PATH}" \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    SCRIBE_TEMP_DIR=/data/tmp \
    SCRIBE_CODEX_BIN=codex

# System deps:
#  - ffmpeg: local audio normalisation (16k mono WAV) before shipping to Vast
#  - openssh-client: scp/ssh into the Vast instance for transcription
#  - ca-certificates, curl, unzip, gnupg: for installing deno + node + codex
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates curl unzip gnupg openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Node 20 (for codex CLI) + deno (yt-dlp player JS extractor).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y \
    && deno --version

# codex CLI — pinned to the version used during MVP development.
RUN npm install -g --no-audit --no-fund @openai/codex@0.130.0 \
    && codex --version

# Python deps — install in two passes so the lockfile layer is cached even
# when only application source changes.
WORKDIR /app
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --frozen --no-install-project 2>/dev/null \
    || uv sync --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN uv sync --frozen 2>/dev/null || uv sync

# scribe expects SCRIBE_TEMP_DIR (default /data/tmp) to exist. The compose
# file mounts a volume here so big audio files don't fill the container fs.
RUN mkdir -p /data/tmp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["uv", "run", "uvicorn", "scribe.main:app", "--host", "0.0.0.0", "--port", "8000"]
