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

    # go.oklabs.uk shortener
    shortlink_base: str = "http://go.oklabs.uk"

    # public URL of this service (for shortlink targets / web-UI links)
    public_base_url: str = "http://localhost:8000"

    worker_concurrency: int = 1


settings = Settings()
