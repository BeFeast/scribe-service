"""Runtime settings, Infisical/env-driven (SCRIBE_* / .env) with a DB overlay."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_network
from typing import Any, Literal

from pydantic import AnyHttpUrl, PrivateAttr, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from scribe.runtime_config import RuntimeConfigError, load_infisical_settings, redact_values

_DEFAULT_SUMMARY_PROVIDERS: tuple[str, ...] = ("codex", "freellmapi")
# Transcription provider chain. Default is Vast-only so behaviour is unchanged
# until an operator opts a fallback in via SCRIBE_TRANSCRIBE_PROVIDERS (see
# scribe.pipeline.transcribe_providers).
_DEFAULT_TRANSCRIBE_PROVIDERS: tuple[str, ...] = ("vast",)

ConfigKind = Literal[
    "bool",
    "float",
    "int",
    "display_currency",
    "prompt_version",
    "short_description_language",
    "text_optional",
    "url",
    "url_optional",
]


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
    "display_currency": RuntimeConfigSpec("display_currency", "display_currency"),
    "short_description_language": RuntimeConfigSpec("short_description_language", "short_description_language"),
    "default_owner_email": RuntimeConfigSpec("default_owner_email", "text_optional"),
    "default_owner_subject": RuntimeConfigSpec("default_owner_subject", "text_optional"),
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
    if spec.kind == "display_currency":
        if not isinstance(value, str) or value.strip().upper() not in {"ILS", "USD", "EUR"}:
            raise ValueError(f"{key} must be one of: ILS, USD, EUR")
        return value.strip().upper()
    if spec.kind == "short_description_language":
        if not isinstance(value, str) or value.strip().lower() not in {"ru", "en"}:
            raise ValueError(f"{key} must be one of: ru, en")
        return value.strip().lower()
    if spec.kind == "text_optional":
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        return value.strip()
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
    transcribe_timeout_secs: int = 1800
    vast_orphan_reaper_max_age_minutes: int = 60
    vast_orphan_reaper_interval_seconds: int = 300
    # Cost-aware reaping (#355). An instance whose live $/hr exceeds
    # `baseline * cost_multiplier` is reaped regardless of age, and a
    # scribe instance stuck in a non-running billable state (loading/starting)
    # past `stuck_minutes` is reaped before max_age. A cost_multiplier <= 0
    # disables cost reaping; a stuck_minutes <= 0 disables stuck reaping.
    vast_orphan_reaper_cost_multiplier: float = 10.0
    vast_orphan_reaper_stuck_minutes: int = 15
    vast_budget_baseline_usd_per_hour: float = 0.05
    vast_budget_alert_multiplier: float = 5.0
    vast_budget_check_interval_seconds: int = 3600
    # Predictive burn alert (#355). When the live burn rate is projected to
    # breach the rolling monthly cap within this horizon (days), emit a
    # Telegram alert + a Prometheus gauge with the projected breach time.
    # `predictive_alert_cooldown_minutes` adds hysteresis so a sustained
    # breach raises at most one alert per cooldown window instead of firing
    # every budget-check cycle.
    vast_budget_predictive_alert_horizon_days: int = 30
    vast_budget_predictive_alert_cooldown_minutes: int = 360

    # Offer-selection tunables. Defaults intentionally permissive so a thin
    # Vast market does not instantly fail jobs (see #254). All overridable
    # via Infisical (SCRIBE_VAST_GPU_REGEX, SCRIBE_VAST_MIN_CUDA, ...).
    vast_gpu_regex: str = (
        r"\b("
        r"RTX\s+3090|RTX\s+4080|RTX\s+4090|"
        r"RTX\s+5060\s+Ti|RTX\s+5070|RTX\s+5080|RTX\s+5090|"
        r"(RTX\s+)?A[2456][05]00|A10|A40|A100(\s+(PCIE|SXM4|SXM5|NVL))?|"
        r"H100(\s+(PCIE|SXM5|NVL))?|H200|"
        r"L4|L40S?|"
        r"RTX\s+(4000|4500|5000|5500|6000)(\s+Ada(\s+Generation)?)?"
        r")\b"
    )
    vast_min_cuda: float = 12.4
    vast_max_price_per_hour: float = 3.0
    vast_max_job_cost: float = 0.25
    vast_instance_ready_timeout: int = 600
    # Number of distinct offers we try in sequence per job. Larger pool helps
    # ride out the offer→ask race (HTTP 400 no_such_ask) and ready-timeout
    # churn without instantly failing the job.
    vast_offer_attempts: int = 12
    # Rolling 30-day hard ceiling on Vast spend (USD). 0 disables. When the
    # cap is reached, the worker refuses to provision new instances and
    # raises WhisperError instead of submitting a new ask.
    vast_monthly_cap_usd: float = 15.0

    # Transcription provider chain (see scribe.pipeline.transcribe_providers).
    # Vast.ai GPU whisper is the primary; optional fallbacks ("openai" hosted
    # API, "local-whisper" CPU faster-whisper) are opt-in, cost-capped, and
    # tried in order when an earlier provider fails. Default is "vast" only —
    # behaviour is unchanged until an operator adds a fallback. Sourced from
    # SCRIBE_TRANSCRIBE_PROVIDERS as a comma-separated, case-insensitive list.
    transcribe_providers: list[str] = list(_DEFAULT_TRANSCRIBE_PROVIDERS)

    # Per-provider transcription circuit breaker. Mirrors the summary breaker
    # but trips faster: a Vast "failure" already represents a whole job's
    # offer loop (up to vast_offer_attempts) giving up, so two consecutive
    # trip-relevant failures within the window are enough to skip Vast and go
    # straight to the fallback for the cooldown window.
    transcribe_breaker_window_secs: int = 900
    transcribe_breaker_threshold: int = 2
    transcribe_breaker_cooldown_secs: int = 600

    # OpenAI hosted transcription fallback ("openai" provider). Opt-in: empty
    # api key leaves the provider permanently unavailable so it never bills
    # silently. Cost-capped per job from the audio duration (whisper-1 is
    # billed per minute); a job whose estimate exceeds the cap is rejected
    # before the upload. Never paste the key into source — supply it via
    # Infisical / SCRIBE_OPENAI_TRANSCRIBE_API_KEY.
    openai_transcribe_api_key: str = ""
    openai_transcribe_base_url: str = "https://api.openai.com/v1"
    openai_transcribe_model: str = "whisper-1"
    openai_transcribe_timeout_secs: int = 600
    openai_transcribe_cost_per_minute_usd: float = 0.006
    openai_transcribe_max_job_cost_usd: float = 0.50

    # Local CPU faster-whisper fallback ("local-whisper" provider). Slow but
    # always available — no GPU, no network. faster-whisper is an optional
    # dependency; if it is not importable the provider reports unavailable and
    # the chain advances. A small model keeps CPU transcription tractable.
    local_whisper_model_size: str = "base"
    local_whisper_compute_type: str = "int8"

    # Summary fallback-chain circuit breaker (see
    # scribe.pipeline.summary_providers.CircuitBreaker). Per-provider in-process
    # state: if the last `threshold` outcomes within `window_secs` are all
    # trip-relevant errors, the provider is skipped for `cooldown_secs`.
    summary_breaker_window_secs: int = 300
    summary_breaker_threshold: int = 3
    summary_breaker_cooldown_secs: int = 600

    # Summary backend — codex CLI (MVP)
    codex_bin: str = "codex"
    # empty = use the codex config.toml model (gpt-5.x family). gpt-5.4-nano/mini
    # are NOT available via a ChatGPT-account codex.
    codex_model: str = ""
    # "minimal" is rejected by the API (codex default tools need >= low).
    codex_reasoning: str = "low"
    codex_timeout_secs: int = 600

    # Lock file ensuring at most one codex invocation runs at a time inside
    # the container. ChatGPT OAuth refresh tokens are single-use; concurrent
    # codex processes would race the refresh and revoke each other's tokens.
    codex_lock_path: str = "/tmp/scribe-codex.lock"

    # Max seconds a summary worker waits to acquire the single-codex lock
    # before giving up and letting the fallback chain advance to the next
    # provider. The lock still spans the whole `codex exec` (the OAuth refresh
    # token rotates mid-exec and codex exposes no separate auth phase), so this
    # bounds a second worker's worst-case wait from the full codex timeout
    # (`codex_timeout_secs`) down to this value instead of globally serialising
    # all summary work. Contention is observable via `scribe_codex_lock_wait_seconds`.
    codex_lock_wait_timeout_secs: int = 120

    # Fallback summary providers tried in order when codex (or any earlier
    # provider) fails. Sourced from SCRIBE_SUMMARY_PROVIDERS as a
    # comma-separated, case-insensitive list. Unknown names are dropped at
    # build-time with a warning so a typo in env does not crash the worker.
    summary_providers: list[str] = list(_DEFAULT_SUMMARY_PROVIDERS)

    # Claude CLI fallback. `claude_bin` resolves via PATH inside the container.
    # `claude_effort` maps to the CLI's --effort flag (xhigh|high|medium|low).
    claude_bin: str = "claude"
    claude_model: str = "opus[1m]"
    claude_effort: str = "xhigh"
    claude_timeout_secs: int = 600

    # FreeLLMAPI proxy fallback. The API key is sourced at runtime by the
    # Infisical loader (`scribe.runtime_config`): it reads project `services`,
    # env `prod`, path `/scribe-service`, and maps secret `FREELLMAPI_API_KEY`
    # onto this field via the field-name normalisation in `_settings_key`.
    # Plain env (`SCRIBE_FREELLMAPI_API_KEY`) also works as a fallback. Never
    # paste the key into source or fixtures. `freellmapi_model` is a
    # placeholder; the provider probes GET ${base_url}/models once and falls
    # back to the configured value if discovery is unavailable.
    freellmapi_base_url: str = "http://10.10.0.13:13032/v1"
    freellmapi_api_key: str = ""
    freellmapi_model: str = "gpt-4o-mini"
    freellmapi_timeout_secs: int = 600

    # Directory containing transcript-summary.v*.md and transcript-summary.active.
    # Operators can bind-mount this path to persist prompt edits across deploys.
    prompt_dir: str = ""

    # Optional admin Telegram channel for operational alerts (e.g. codex
    # token revoked, summarizer down). Both must be set to enable.
    admin_telegram_bot_token: str = ""
    admin_telegram_chat_id: str = ""

    # Nightly yt-dlp download canary. Exercises the real download path against
    # a known-stable public video (default: "Me at the zoo" — the first YouTube
    # upload, kept online for historical reasons). A red canary means the pin
    # or YouTube changed under us; see docs/runbooks/download-canary.md.
    download_canary_enabled: bool = True
    download_canary_url: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    download_canary_interval_seconds: int = 86_400
    download_canary_initial_delay_seconds: int = 600
    download_canary_runbook_url: str = (
        "https://github.com/BeFeast/scribe-service/blob/main/docs/runbooks/download-canary.md"
    )

    # bgutil PO-token provider sidecar (#309). Empty disables the integration
    # so yt-dlp falls back to its old token-free clients. The default points at
    # the in-stack sidecar name (`scribe-pot`) on the scribe network; override
    # via SCRIBE_BGUTIL_POT_BASE_URL if the sidecar runs elsewhere. The
    # bgutil-ytdlp-pot-provider plugin (installed via pyproject) is auto-
    # discovered by yt-dlp; downloader.py forwards this value as
    # `--extractor-args "youtubepot-bgutilhttp:base_url=…"`.
    bgutil_pot_base_url: str = "http://scribe-pot:4416"

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
    app_start_workers: bool = True

    # Daily Vast.ai spend cap in USD (rolling 24h). 0 disables the cap.
    # When exceeded, POST /jobs returns 429 until the rolling window opens up.
    daily_spend_cap_usd: float = 0.0

    # Runtime-tunable Settings page knobs. These default from env/.env and may
    # be overlaid from app_config at startup or after POST /api/config.
    config_api_bearer_token: str = ""
    auth_allowed_emails: str = ""
    auth_clerk_issuer: str = ""
    auth_clerk_jwks_url: str = ""
    auth_clerk_jwks_json: str = ""
    clerk_publishable_key: str = ""
    clerk_frontend_api: str = ""
    clerk_backend_api_url: str = "https://api.clerk.com"
    clerk_secret_key: str = ""
    bootstrap_admin_email: str = ""
    auth_test_mode: bool = False
    bot_wall_retry: bool = False
    webhook_default: str = ""
    webhook_embed_transcript: bool = False
    prompt_template_active_version: str = "v1"
    display_currency: str = "ILS"
    short_description_language: str = "ru"
    default_owner_email: str = ""
    default_owner_subject: str = ""

    # These belong to the auth layer from #104/#105. They are env-only here:
    # the owner attribution code consumes them without exposing bearer secrets
    # through the mutable config API.
    machine_bearer_token: str = ""
    # Grace window (seconds) during which the previous-generation machine
    # bearer token is still accepted after a rotation (see
    # scribe.api.tokens). 0 disables the grace window: a rotation rejects the
    # old token on the very next request.
    machine_bearer_grace_seconds: int = 300
    trusted_cidrs: str = "127.0.0.0/8,::1/128"

    # Path the scribe-backups sidecar writes after each successful run; surfaced
    # by GET /admin/backup-status for healthcheck curl-polling (PRD §4.12).
    # The default lives on a volume that scribe-backups bind-mounts; for the
    # scribe API to see it, the same path must be mounted into the scribe
    # container (read-only is fine).
    backup_status_path: str = "/backups/_last_success_ts"
    # Backups run nightly; flag as stale once the heartbeat is >25h old (one
    # missed cron tick). 0 disables the staleness check.
    backup_stale_after_seconds: int = 90_000

    @field_validator("summary_providers", mode="before")
    @classmethod
    def _parse_summary_providers(cls, value: Any) -> list[str]:
        """Accept either a comma-separated env string or a list. Names are
        lowercased and stripped; unknown names are kept (callers reject them
        at chain-build time with a clear error)."""
        if value is None or value == "":
            return list(_DEFAULT_SUMMARY_PROVIDERS)
        if isinstance(value, str):
            parts = [p.strip().lower() for p in value.split(",")]
            return [p for p in parts if p]
        if isinstance(value, list):
            return [str(p).strip().lower() for p in value if str(p).strip()]
        raise ValueError("summary_providers must be a list or comma-separated string")

    @field_validator("transcribe_providers", mode="before")
    @classmethod
    def _parse_transcribe_providers(cls, value: Any) -> list[str]:
        """Accept a comma-separated env string or a list. Names are lowercased
        and stripped; unknown names are kept (the chain builder rejects them at
        build time with a clear error). Empty input restores the Vast-only
        default so a blanked env var cannot silently disable transcription."""
        if value is None or value == "":
            return list(_DEFAULT_TRANSCRIBE_PROVIDERS)
        if isinstance(value, str):
            parts = [p.strip().lower() for p in value.split(",")]
            return [p for p in parts if p]
        if isinstance(value, list):
            return [str(p).strip().lower() for p in value if str(p).strip()]
        raise ValueError("transcribe_providers must be a list or comma-separated string")

    @field_validator("trusted_cidrs")
    @classmethod
    def _validate_trusted_cidrs(cls, value: str) -> str:
        for raw in value.split(","):
            cidr = raw.strip()
            if not cidr:
                continue
            try:
                ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"SCRIBE_TRUSTED_CIDRS contains invalid CIDR {cidr!r}") from exc
        return value

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


def build_settings() -> Settings:
    """Build process settings with Infisical taking precedence over env.

    Empty Infisical values are dropped from the overlay so a missing/blank
    secret cannot clobber a valid env var (see #261). In the resilient
    sidecar setup the Agent renders secrets into an env-file that the
    container entrypoint sources before pydantic runs, making the in-process
    fetch redundant; this guard makes the redundancy safe.
    """
    overlay = {
        key: value
        for key, value in load_infisical_settings(Settings.model_fields).items()
        if value != ""
    }
    try:
        return Settings(**overlay)
    except ValidationError as exc:
        if not overlay:
            raise
        raise RuntimeConfigError(redact_values(str(exc), overlay.values())) from None


settings = build_settings()
