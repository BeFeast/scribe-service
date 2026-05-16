"""Runtime settings, env-driven (SCRIBE_* / .env) with a DB overlay."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import AnyHttpUrl, PrivateAttr, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ConfigKind = Literal["bool", "float", "int", "prompt_version", "url", "url_optional"]


@dataclass(frozen=True)
class RuntimeConfigSpec:
    key: str
    kind: ConfigKind
    mutable: bool = True
    restart_required: bool = False


RUNTIME_CONFIG: dict[str, RuntimeConfigSpec] = {
    "daily_spend_cap_usd": RuntimeConfigSpec("daily_spend_cap_usd", "float"),
    "worker_concurrency": RuntimeConfigSpec(
        "worker_concurrency", "int", restart_required=True
    ),
    "bot_wall_retry": RuntimeConfigSpec("bot_wall_retry", "bool"),
    "webhook_default": RuntimeConfigSpec("webhook_default", "url_optional"),
    "webhook_embed_transcript": RuntimeConfigSpec("webhook_embed_transcript", "bool"),
    "public_base_url": RuntimeConfigSpec("public_base_url", "url"),
    "prompt_template_active_version": RuntimeConfigSpec(
        "prompt_template_active_version", "prompt_version"
    ),
}

_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_PROMPT_VERSIONS = frozenset({"v1", "v2", "v3"})


def parse_runtime_config_value(key: str, value: Any) -> bool | float | int | str:
    spec = RUNTIME_CONFIG[key]
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"{key} must be a boolean")
    if spec.kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{key} must be an integer")
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or any(ch in stripped for ch in ".eE"):
                raise ValueError(f"{key} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if parsed < 1:
            raise ValueError(f"{key} must be >= 1")
        return parsed
    if spec.kind == "float":
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"{key} must be finite")
        if parsed < 0:
            raise ValueError(f"{key} must be >= 0")
        return parsed
    if spec.kind == "prompt_version":
        if not isinstance(value, str) or value not in _PROMPT_VERSIONS:
            raise ValueError(f"{key} must be one of: v1, v2, v3")
        return value
    if spec.kind == "url_optional":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a URL string")
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return str(_URL_ADAPTER.validate_python(stripped))
        except ValidationError as exc:
            raise ValueError(f"{key} must be a valid URL") from exc
    if spec.kind == "url":
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a URL string")
        try:
            return str(_URL_ADAPTER.validate_python(value.strip()))
        except ValidationError as exc:
            raise ValueError(f"{key} must be a valid URL") from exc
    raise ValueError(f"unsupported config kind for {key}")


def serialize_runtime_config_value(value: bool | float | int | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRIBE_", env_file=".env", extra="ignore")
    _runtime_sources: set[str] = PrivateAttr(default_factory=set)

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

    # Runtime-tunable Settings page knobs. These default from env/.env and may
    # be overlaid from app_config at startup or after POST /api/config.
    config_api_bearer_token: str = ""
    bot_wall_retry: bool = False
    webhook_default: str = ""
    webhook_embed_transcript: bool = False
    prompt_template_active_version: str = "v1"

    # Path the scribe-backups sidecar writes after each successful run; surfaced
    # by GET /admin/backup-status for healthcheck curl-polling (PRD §4.12).
    # The default lives on a volume that scribe-backups bind-mounts; for the
    # scribe API to see it, the same path must be mounted into the scribe
    # container (read-only is fine).
    backup_status_path: str = "/backups/_last_success_ts"
    # Backups run nightly; flag as stale once the heartbeat is >25h old (one
    # missed cron tick). 0 disables the staleness check.
    backup_stale_after_seconds: int = 90_000

    def runtime_overlay(self, rows: Mapping[str, str] | None = None) -> set[str]:
        """Overlay mutable runtime config from app_config and return DB-backed keys."""
        if rows is None:
            from sqlalchemy import select

            from scribe.db.models import AppConfig
            from scribe.db.session import SessionLocal

            with SessionLocal() as session:
                rows = dict(session.execute(select(AppConfig.key, AppConfig.value)).all())

        applied: set[str] = set()
        for key, raw_value in rows.items():
            if key not in RUNTIME_CONFIG:
                continue
            setattr(self, key, parse_runtime_config_value(key, raw_value))
            applied.add(key)
        self._runtime_sources = applied
        return applied

    def runtime_source(self, key: str) -> Literal["env", "db"]:
        return "db" if key in self._runtime_sources else "env"


settings = Settings()
