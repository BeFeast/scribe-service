"""Runtime settings, env-driven (SCRIBE_* / .env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRIBE_", env_file=".env", extra="ignore")

    # Postgres (db-dev on DevBox)
    database_url: str = "postgresql+psycopg://scribe:scribe@localhost:5432/scribe"

    # Transient audio scratch — NFS mount of TrueNAS in prod (see design doc)
    temp_dir: str = "/data/tmp"

    # Vast.ai — whisper only
    vast_api_key: str = ""

    # Summary backend — codex CLI (MVP)
    codex_bin: str = "codex"
    # empty = use the codex config.toml model (gpt-5.x family). gpt-5.4-nano/mini
    # are NOT available via a ChatGPT-account codex.
    codex_model: str = ""
    # "minimal" is rejected by the API (codex default tools need >= low).
    codex_reasoning: str = "low"

    # Lock file ensuring at most one codex invocation runs at a time inside
    # the container. ChatGPT OAuth refresh tokens are single-use; concurrent
    # codex processes would race the refresh and revoke each other's tokens.
    codex_lock_path: str = "/tmp/scribe-codex.lock"

    # Directory containing transcript-summary.v*.md and transcript-summary.active.
    # Operators can bind-mount this path to persist prompt edits across deploys.
    prompt_dir: str = ""

    # Optional admin Telegram channel for operational alerts (e.g. codex
    # token revoked, summarizer down). Both must be set to enable.
    admin_telegram_bot_token: str = ""
    admin_telegram_chat_id: str = ""

    # go.oklabs.uk shortener (Chhoto on Edgebox). api_url/api_key are env-driven
    # (never hardcode credentials); shortlink_base is the public resolver host.
    shortlink_base: str = "http://go.oklabs.uk"
    shortlink_api_url: str = ""
    shortlink_api_key: str = ""

    # public URL of this service (for shortlink targets / web-UI links)
    public_base_url: str = "http://localhost:8000"

    # Default 2: scribe is single-user + LAN-only; codex is flock-serialised and
    # each whisper run owns its own Vast instance, so two in flight is safe and
    # halves wall time on a small batch.
    worker_concurrency: int = 2

    # Daily Vast.ai spend cap in USD (rolling 24h). 0 disables the cap.
    # When exceeded, POST /jobs returns 429 until the rolling window opens up.
    daily_spend_cap_usd: float = 0.0

    # Path the scribe-backups sidecar writes after each successful run; surfaced
    # by GET /admin/backup-status for healthcheck curl-polling (PRD §4.12).
    # The default lives on a volume that scribe-backups bind-mounts; for the
    # scribe API to see it, the same path must be mounted into the scribe
    # container (read-only is fine).
    backup_status_path: str = "/backups/_last_success_ts"
    # Backups run nightly; flag as stale once the heartbeat is >25h old (one
    # missed cron tick). 0 disables the staleness check.
    backup_stale_after_seconds: int = 90_000


settings = Settings()
