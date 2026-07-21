"""Runtime settings, Infisical/env-driven (SCRIBE_* / .env) with a DB overlay."""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_network
from typing import Any, Literal

from pydantic import AnyHttpUrl, PrivateAttr, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from scribe.runtime_config import (
    InfisicalBootPolicy,
    OverlayState,
    RuntimeConfigError,
    load_infisical_overlay,
    load_infisical_overlay_with_retry,
    redact_values,
)

# Default summary fallback chain (#388). Entries are `provider:model`; the
# generic OpenAI-compatible HTTP backend (scribe.pipeline.summary_providers)
# is used several times with different models. Direct HTTP keeps summaries off
# the heavy codex/claude CLI harnesses (single-codex-lock, ChatGPT model caps).
# codex/claude stay available — append e.g. `codex` to SCRIBE_SUMMARY_PROVIDERS.
_DEFAULT_SUMMARY_PROVIDERS: tuple[str, ...] = (
    "ollama-cloud:glm-5.2",
    "ollama-cloud:gemma4:31b",
    "freellmapi:gemini-2.5-flash",
)
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
    "lan_youtube_cookies_enabled": RuntimeConfigSpec("lan_youtube_cookies_enabled", "bool"),
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

    # Wall-clock budget for the yt-dlp download stage (#346). yt-dlp can hang
    # indefinitely on a stuck stream/network read and pin a worker thread for
    # hours; this bounds a single download_audio call. On expiry the yt-dlp
    # process group is SIGKILLed (no orphan) and the job fails with a typed
    # DownloadError(reason=download_timeout). 0 disables the timeout (not
    # recommended — see MAX_TOTAL_BACKOFF_SECONDS only bounds backoff sleeps,
    # not a hung subprocess read).
    download_timeout_s: int = 600

    # Hard ceiling on a single downloaded media file (#416). Passed to yt-dlp as
    # --max-filesize so a direct-media URL (or any extractor) that points at an
    # oversize stream is aborted instead of filling the scratch disk. Audio-only
    # YouTube pulls sit far under this, so the default leaves existing behavior
    # unchanged; a value <= 0 disables the cap (tests/canary only).
    download_max_bytes: int = 2 * 1024 * 1024 * 1024

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
    # behaviour is unchanged until an operator adds a fallback.
    #
    # Canonical configuration path is Infisical (env `prod`, path
    # `/scribe-service`) via the in-process overlay (`build_settings` passes
    # the value as a kwarg into `Settings(...)`, which hits the
    # `field_validator(mode="before")` directly): there the value is a
    # comma-separated, case-insensitive list, e.g. `vast,openai,local-whisper`.
    #
    # Via raw env (`SCRIBE_TRANSCRIBE_PROVIDERS`) pydantic-settings' env source
    # JSON-decodes list-type fields BEFORE the validator runs, so a bare
    # comma-separated string (`vast,openai`) fails JSON parsing and raises
    # `SettingsError` at boot. Raw env MUST be a JSON array, e.g.
    # `["vast","openai","local-whisper"]`.
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

    # Hard ceiling on accepted summary output size in characters. Pathological
    # LLM output (infinite repetition, prompt-injection) can produce multi-MB
    # summaries that bloat Postgres and slow the SPA. A response exceeding the
    # cap is rejected by `validate_and_canonicalize` with reason
    # `summary_too_large` so the chain advances / the job is marked
    # summary-failed instead of persisting an oversized blob. Default ~100k
    # chars; 0 disables the cap (not recommended).
    max_summary_chars: int = 100_000

    # Map-reduce chunking for oversized summary inputs (#382). When the built
    # prompt (template + transcript) exceeds `summary_map_reduce_chars`, the
    # chain summarises the transcript in chunks (map) and merges the partials
    # in a final pass (reduce) instead of POSTing the whole body at once — this
    # keeps payload-limited backends (freellmapi returns 413
    # PayloadTooLargeError) producing a valid summary on long videos. The
    # default threshold leaves headroom under the freellmapi limit; short
    # transcripts stay on the single-pass path with no extra calls. 0 disables
    # map-reduce entirely (always single pass). `chunk_chars` caps the
    # transcript characters fed to each map call; `overlap_chars` repeats a
    # little context across chunk boundaries so a thought split across a
    # boundary is not lost.
    summary_map_reduce_chars: int = 80_000
    summary_map_reduce_chunk_chars: int = 60_000
    summary_map_reduce_overlap_chars: int = 500

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

    # Summary fallback chain tried in order. Each entry is a `provider:model`
    # pair (#388), e.g. `ollama-cloud:glm-5.2,ollama-cloud:gemma4:31b,
    # freellmapi:gemini-2.5-flash,codex`. Each entry is split on the FIRST ':'
    # — provider name (case-insensitive) and model (verbatim, so tags like
    # `gemma4:31b` survive). A bare provider name with no ':' uses that
    # provider's default model (backward compatible with the old name-only
    # format). Unknown provider names are rejected at chain-build time so a
    # typo surfaces loudly.
    #
    # Canonical configuration path is Infisical (env `prod`, path
    # `/scribe-service`) via the in-process overlay (`build_settings` passes
    # the value as a kwarg into `Settings(...)`, which hits the
    # `field_validator(mode="before")` directly): there the value is a
    # comma-separated list as shown above.
    #
    # Via raw env (`SCRIBE_SUMMARY_PROVIDERS`) pydantic-settings' env source
    # JSON-decodes list-type fields BEFORE the validator runs, so a bare
    # comma-separated string (`ollama-cloud:glm-5.2,codex`) fails JSON parsing
    # and raises `SettingsError: error parsing value for field summary_providers`
    # at boot. Raw env MUST be a JSON array, e.g.
    # `["ollama-cloud:glm-5.2","codex"]`.
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

    # Ollama Cloud / OpenAI-compatible HTTP backend (#388). flat-subscription,
    # zero marginal cost; catalogue includes glm-5.2, gemma4:* and minimax-m3
    # (1M context for very long transcripts). `ollama_base_url` is the
    # OpenAI-compatible endpoint (local signed-in daemon `…:11434/v1` or the
    # cloud API — set by ops). Empty base URL = provider Unavailable so the chain
    # advances (no key/URL ⇒ skip, never crash). `ollama_api_key` is optional: a
    # local signed-in daemon needs none, so the provider does not require it.
    # `ollama_model` is the default model used when a chain entry omits one.
    ollama_base_url: str = ""
    ollama_api_key: str = ""
    ollama_model: str = "glm-5.2"
    ollama_timeout_secs: int = 600

    # Directory containing transcript-summary.v*.md and transcript-summary.active.
    # Operators can bind-mount this path to persist prompt edits across deploys.
    prompt_dir: str = ""

    # Optional admin Telegram channel for operational alerts (e.g. codex
    # token revoked, summarizer down). Both must be set to enable.
    admin_telegram_bot_token: str = ""
    admin_telegram_chat_id: str = ""

    # Consumer-facing Telegram media ingestion (#417). When a Telegram user
    # sends media too large for the bot's inline download path, the integration
    # submits an opaque `tg:<file_id>` reference through POST /jobs; the worker
    # resolves it here via the Bot API `getFile` + file download. The token is
    # the ONLY credential allowed to resolve the reference — it lives in server
    # config (Infisical / SCRIBE_TELEGRAM_BOT_TOKEN), is scrubbed from every log
    # line (see scribe.obs.logging._SECRET_SETTING_FIELDS), and is never placed
    # in a job record, API payload, or error message. Empty disables the path:
    # a `tg:` submission then fails with an actionable "not configured" error.
    #
    # `telegram_api_base_url` defaults to the public Bot API, whose download
    # limit is 20 MB. To ingest large media (up to 2 GB) point this at a
    # self-hosted `telegram-bot-api` server; in its `--local` mode `getFile`
    # returns an on-disk path that the adapter reads directly with no HTTP
    # download. See docs/telegram-media-ingestion.md.
    telegram_bot_token: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    # Wall-clock budget for a single Telegram getFile+download stage.
    telegram_download_timeout_s: int = 600
    # Defence-in-depth ceiling on a resolved Telegram file size (bytes). The
    # public Bot API already caps at 20 MB; a local Bot API server lifts that to
    # 2 GB. This bound rejects an oversize `file_size` from getFile before the
    # download starts, mirroring upload_max_bytes for the upload path.
    telegram_max_bytes: int = 2 * 1024 * 1024 * 1024

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

    # LAN gated-video opt-in (#405). Off by default so multi-user/public
    # deployments keep #308's strict owner gate on `youtube_cookies`. When on,
    # a plain trusted-LAN actor (no Clerk sign-in) may attach `youtube_cookies`
    # to POST /jobs; the job is attributed to the default owner via
    # `current_owner`. Machine-bearer (shared infra credential) and non-LAN
    # callers stay rejected. Cookies remain per-job ephemeral (never persisted
    # or logged) exactly as on the owner path.
    lan_youtube_cookies_enabled: bool = False

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
    # Trusted reverse-proxy peers (#348). Comma-separated CIDRs/IPs whose
    # direct peer connection we trust to have appended the real client IP to
    # `X-Forwarded-For`. When unset (empty), XFF is never honoured and the
    # immediate peer IP is used for CIDR auth + logging (safe default). When
    # set, XFF is walked right-to-left skipping trusted proxies so a client
    # cannot spoof the leftmost entry.
    trusted_proxies: str = ""

    # Upload-your-own-video archival media store (#408). Users can upload a
    # local video/audio file (POST /jobs/upload); after the normal transcript +
    # summary, a downscaled archival copy is transcoded and stored in a private
    # Cloudflare R2 bucket (S3-compatible), retrievable via presigned URL only.
    # The feature is OFF unless all four R2 credentials are set — the upload
    # endpoint returns 503 with a clear message when unconfigured (see
    # scribe.pipeline.media_store.is_configured). Secrets come from Infisical
    # (SCRIBE_MEDIA_S3_* -> settings). YouTube/URL jobs never archive media.
    media_s3_endpoint: str = ""
    media_s3_bucket: str = ""
    media_s3_access_key: str = ""
    media_s3_secret_key: str = ""
    # R2 ignores the region but boto3's SigV4 signer requires one; "auto" is
    # the value Cloudflare documents for the S3 API.
    media_s3_region: str = "auto"
    # TTL (seconds) of the presigned GET URL handed out by
    # GET /transcripts/{id}/media. ~1h per the design.
    media_presign_ttl_seconds: int = 3600
    # Hard ceiling on an accepted upload body. Default 4 GiB; oversize uploads
    # are rejected with 413 before entering the pipeline. API-contract style
    # constant precedent is YOUTUBE_COOKIES_MAX_BYTES in api/schemas.py, but a
    # size this large is an operator knob, so it lives in settings.
    upload_max_bytes: int = 4 * 1024 * 1024 * 1024

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
        """Normalise a `provider:model` entries value into a list.

        Accepts a comma-separated string OR a list. This validator runs in
        `mode="before"` and is reached via TWO different paths with different
        input shapes:

        - Infisical overlay (`build_settings` passes the value as a kwarg into
          `Settings(...)`): the raw secret string reaches here verbatim, so a
          comma-separated value like `ollama-cloud:glm-5.2,codex` works.
        - Raw env (`SCRIBE_SUMMARY_PROVIDERS`): pydantic-settings' env source
          JSON-decodes list-type fields BEFORE this validator runs, so the env
          value MUST be a JSON array (`["ollama-cloud:glm-5.2","codex"]`); a
          bare comma-separated string fails JSON parsing and crash-loops boot
          with `SettingsError`.

        Entries are stripped but kept verbatim — the entry is NOT lowercased
        here because the model part is case-sensitive (e.g. `gemma4:31b`). The
        provider name is lowercased case-insensitively at chain-build time,
        which also rejects unknown providers with a clear error."""
        if value is None or value == "":
            return list(_DEFAULT_SUMMARY_PROVIDERS)
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",")]
            return [p for p in parts if p]
        if isinstance(value, list):
            return [str(p).strip() for p in value if str(p).strip()]
        raise ValueError("summary_providers must be a list or comma-separated string")

    @field_validator("transcribe_providers", mode="before")
    @classmethod
    def _parse_transcribe_providers(cls, value: Any) -> list[str]:
        """Normalise a transcription provider chain value into a list.

        Accepts a comma-separated string OR a list. This validator runs in
        `mode="before"` and is reached via TWO different paths with different
        input shapes:

        - Infisical overlay (`build_settings` passes the value as a kwarg into
          `Settings(...)`): the raw secret string reaches here verbatim, so a
          comma-separated value like `vast,openai` works.
        - Raw env (`SCRIBE_TRANSCRIBE_PROVIDERS`): pydantic-settings' env
          source JSON-decodes list-type fields BEFORE this validator runs, so
          the env value MUST be a JSON array (`["vast","openai"]`); a bare
          comma-separated string fails JSON parsing and crash-loops boot with
          `SettingsError`.

        Names are lowercased and stripped; unknown names are kept (the chain
        builder rejects them at build time with a clear error). Empty input
        restores the Vast-only default so a blanked env var cannot silently
        disable transcription."""
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

    @field_validator("trusted_proxies")
    @classmethod
    def _validate_trusted_proxies(cls, value: str) -> str:
        for raw in value.split(","):
            entry = raw.strip()
            if not entry:
                continue
            try:
                ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(f"SCRIBE_TRUSTED_PROXIES contains invalid CIDR/IP {entry!r}") from exc
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


def _running_under_pytest() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def _record_runtime_config_state(state: OverlayState) -> None:
    """Publish the boot load state as a Prometheus gauge (#415). Best-effort:
    a metrics import failure must never block startup."""
    try:
        from scribe.obs.metrics import runtime_config_load_state
    except Exception:  # pragma: no cover - metrics optional at import time
        return
    for name in ("infisical", "disabled", "degraded"):
        runtime_config_load_state.labels(state=name).set(1.0 if name == state else 0.0)


def build_settings(policy: InfisicalBootPolicy | None = None) -> Settings:
    """Build process settings with Infisical taking precedence over env.

    Empty Infisical values are dropped from the overlay so a missing/blank
    secret cannot clobber a valid env var (see #261). In the resilient
    sidecar setup the Agent renders secrets into an env-file that the
    container entrypoint sources before pydantic runs, making the in-process
    fetch redundant; this guard makes the redundancy safe.

    Boot hardening (#415): when Infisical is enabled but unreachable, a transient
    outage must not silently leave the process on env fallback (empty provider
    credentials) for its whole lifetime. We retry with bounded exponential
    backoff and then fail fast — raise, so the process exits non-zero and Docker's
    restart policy converges once Infisical recovers — unless the operator sets
    ``SCRIBE_INFISICAL_FAIL_FAST=false`` to run degraded on env fallback. Either
    way the degraded state is surfaced via a log line and the
    ``scribe_runtime_config_load_state`` metric. See
    docs/runbooks/infisical-boot-fallback.md.
    """
    if policy is None:
        policy = InfisicalBootPolicy.from_env()
        if _running_under_pytest():
            policy = policy.without_boot_hardening()

    load = load_infisical_overlay_with_retry(
        Settings.model_fields,
        policy,
        fetch=lambda keys: load_infisical_overlay(keys),
        sleep=time.sleep,
    )

    if load.state == "degraded":
        _record_runtime_config_state("degraded")
        if policy.fail_fast:
            raise RuntimeConfigError(
                "infisical is enabled but unreachable after boot retries; refusing to "
                "start on env fallback (set SCRIBE_INFISICAL_FAIL_FAST=false to run "
                "degraded). See docs/runbooks/infisical-boot-fallback.md"
            )
        logging.getLogger("scribe.runtime_config").error(
            "running on env fallback (DEGRADED): infisical enabled but unreachable at "
            "boot; provider credentials may be missing and summaries will fail until "
            "infisical recovers or the process restarts. See "
            "docs/runbooks/infisical-boot-fallback.md"
        )

    overlay = {key: value for key, value in load.overlay.items() if value != ""}
    try:
        settings_obj = Settings(**overlay)
    except ValidationError as exc:
        if not overlay:
            raise
        raise RuntimeConfigError(redact_values(str(exc), overlay.values())) from None

    if load.state != "degraded":
        _record_runtime_config_state(load.state)
    return settings_obj


settings = build_settings()
