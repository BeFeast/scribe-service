"""Prometheus metrics for scribe.

Exposed on `/metrics` (api/routes.py). Importing this module is enough to
register the collectors; instrumentation hooks live throughout the pipeline.

Naming follows Prometheus conventions: `scribe_*`, snake_case, units as suffix.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest

# Job lifecycle counters.
job_status_transitions = Counter(
    "scribe_job_status_transitions_total",
    "Job status transitions, labelled by terminal status (done/failed) or "
    "intermediate stage (downloading/transcribing/summarizing).",
    labelnames=("status",),
)

# Transcript inserts: full = inserted with summary; partial = inserted with
# summary_md NULL (whisper done, summary pending or failed); promoted = an
# existing partial got its summary populated.
transcripts_total = Counter(
    "scribe_transcripts_total",
    "Transcript rows by insertion kind.",
    labelnames=("kind",),
)

# Archival media (upload jobs, #408). Outcome is ok / failed / retry_ok — a
# failed archive is a SOFT failure (transcript + summary already persisted), so
# this counter is the signal that the R2 store or transcode is degraded without
# the job hard-failing.
media_archive_total = Counter(
    "scribe_media_archive_total",
    "Archival media transcode+upload outcomes for uploaded sources.",
    labelnames=("outcome",),
)

# Stage timing. Buckets tuned for the observed distribution: download/ffmpeg
# are seconds, whisper is minutes (cold-start + transcription), summary is
# tens of seconds to a couple of minutes.
stage_duration_seconds = Histogram(
    "scribe_stage_duration_seconds",
    "End-to-end wall time per pipeline stage.",
    labelnames=("stage",),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

# Cumulative Vast spend (USD). Incremented per successful whisper run with
# the cost returned by whisper_client.TranscribeResult.vast_cost.
vast_spend_usd_total = Counter(
    "scribe_vast_spend_usd_total",
    "Cumulative estimated Vast.ai spend in USD.",
)

# Stale scribe-labelled Vast instances destroyed by the orphan reaper.
# Incremented before DELETE so failed API attempts are visible too.
vast_orphans_destroyed_total = Counter(
    "scribe_vast_orphans_destroyed_total",
    "Stale scribe-labelled Vast.ai instances the orphan reaper attempted to destroy.",
)

# Rolling 24h Vast spend (USD) — same window the daily-spend cap uses.
# Sampled from the DB on each /metrics scrape so alert rules can compare
# directly against SCRIBE_DAILY_SPEND_CAP_USD.
daily_spend_usd = Gauge(
    "scribe_daily_spend_usd",
    "Rolling 24h Vast.ai spend in USD (same window as the daily cap).",
)

# Current rolling 24h spend as a percentage of SCRIBE_DAILY_SPEND_CAP_USD.
# 0 when the cap is disabled (cap <= 0). Prometheus alert rule fires at >= 80.
daily_spend_cap_pct = Gauge(
    "scribe_daily_spend_cap_pct",
    "Rolling 24h Vast spend as a percent of the daily cap; 0 when cap disabled.",
)

vast_burn_rate_usd_per_hour = Gauge(
    "scribe_vast_burn_rate_usd_per_hour",
    "Current Vast.ai burn rate in USD/hour, sampled from live instances.",
)

# Predictive burn projection (#355). Unix epoch of the projected cap-breach
# time at the current live burn rate, or -1 when no breach is projected.
vast_burn_projected_breach_timestamp_seconds = Gauge(
    "scribe_vast_burn_projected_breach_timestamp_seconds",
    "Unix epoch of the projected Vast monthly-cap breach at the current "
    "live burn rate; -1 when no breach is projected.",
)
vast_burn_projected_breach_timestamp_seconds.set(-1)

# Hours remaining until the projected cap breach at the current burn rate,
# or -1 when no breach is projected. Lets alert rules fire on a horizon.
vast_burn_hours_to_cap = Gauge(
    "scribe_vast_burn_hours_to_cap",
    "Hours until the rolling monthly Vast cap is breached at the current "
    "live burn rate; -1 when no breach is projected.",
)
vast_burn_hours_to_cap.set(-1)


def compute_daily_spend_cap_pct(spent_usd: float, cap_usd: float) -> float:
    """Percent of cap consumed. Returns 0.0 when the cap is disabled (<=0)."""
    if cap_usd <= 0:
        return 0.0
    return (spent_usd / cap_usd) * 100.0


# Queue depth — number of non-terminal jobs. Sampled on each /metrics scrape.
worker_queue_depth = Gauge(
    "scribe_worker_queue_depth",
    "Jobs in non-terminal states (queued/downloading/transcribing/summarizing).",
)

# Unix epoch of the most recent done job; -1 if none yet.
last_success_timestamp = Gauge(
    "scribe_last_success_timestamp_seconds",
    "Unix epoch of the most recent job that reached status=done.",
)
last_success_timestamp.set(-1)

# Worker pool busy count — how many of the configured workers are currently
# running `process_job`. inc()/dec() bracket the call in worker/loop.py so
# the gauge is process-wide and observable via /metrics + /api/ops.
workers_busy = Gauge(
    "scribe_workers_busy",
    "Number of worker threads currently inside process_job (vs idle in poll loop).",
)

# Unix epoch of the most recent successful Vast.ai instance launch (whisper
# transcription returned a result). -1 if none yet. Used by the ops rollcall
# to flag the Vast.ai service as `warn` when no recent launches have landed.
last_vast_launch_timestamp = Gauge(
    "scribe_last_vast_launch_timestamp_seconds",
    "Unix epoch of the most recent successful Vast.ai whisper launch.",
)
last_vast_launch_timestamp.set(-1)

# Unix epoch of the most recent successful codex summarizer call. -1 if none
# yet. Used by the ops rollcall to flag the codex CLI as `warn` after >1h of
# silence.
last_codex_success_timestamp = Gauge(
    "scribe_last_codex_success_timestamp_seconds",
    "Unix epoch of the most recent successful codex summarizer call.",
)
last_codex_success_timestamp.set(-1)

# Codex OAuth-token revocations detected in stderr. A spike here means the
# operator needs to re-login (`docker exec -it scribe codex login --device-auth`).
codex_token_revoked_total = Counter(
    "scribe_codex_token_revoked_total",
    "Times codex stderr matched an OAuth-token-revocation signature.",
)

# Seconds a summary worker spent waiting to acquire the single-codex flock
# before either running `codex exec` or (on timeout) falling through to the
# next provider. The lock spans the whole exec because ChatGPT OAuth refresh
# tokens are single-use; this histogram makes the resulting contention between
# concurrent workers observable. Observations near codex_lock_wait_timeout_secs
# mean workers are serialising on codex and falling back instead of waiting.
codex_lock_wait_seconds = Histogram(
    "scribe_codex_lock_wait_seconds",
    "Seconds a summary worker waited to acquire the single-codex lock.",
    buckets=(0.01, 0.1, 1, 5, 15, 30, 60, 120, 300, 600),
)

# Webhook delivery — one increment per terminal-status push attempt.
# outcome: ok (2xx), http_error (non-2xx), net_error (timeout / refused / DNS),
# skipped (callback_url is NULL).
webhook_deliveries_total = Counter(
    "scribe_webhook_deliveries_total",
    "Webhook delivery attempts, labelled by outcome.",
    labelnames=("outcome",),
)

# Wall time of the urlopen call for a successful webhook delivery. Failures
# (network error, non-2xx, malformed URL) are intentionally excluded so the
# histogram reflects the latency distribution of receivers that actually
# accepted the push.
webhook_delivery_latency_seconds = Histogram(
    "scribe_webhook_delivery_latency_seconds",
    "Wall time of urlopen for successful webhook deliveries.",
    buckets=(.05, .1, .25, .5, 1, 2.5, 5, 10),
)

# Webhook attempts — one increment per POST attempt.
# outcome: ok (2xx), http_error (non-2xx), net_error (timeout / refused / DNS).
webhook_attempts_total = Counter(
    "scribe_webhook_attempts_total",
    "Webhook POST attempts, labelled by outcome.",
    labelnames=("outcome",),
)

# Summary-provider chain telemetry. The fallback chain calls each provider in
# order; the circuit breaker (scribe.pipeline.summary_providers) short-circuits
# providers that have failed often enough.
#
# result labels: success, usage_limit, unavailable, timeout, error, skipped_tripped.
summary_provider_calls_total = Counter(
    "scribe_summary_provider_calls_total",
    "Summary provider invocations, labelled by provider and outcome.",
    labelnames=("provider", "result"),
)

# Per-provider breaker state. 0=closed, 1=half_open, 2=tripped.
summary_provider_state = Gauge(
    "scribe_summary_provider_state",
    "Circuit-breaker state per summary provider (0=closed, 1=half_open, 2=tripped).",
    labelnames=("provider",),
)

# yt-dlp download canary — nightly end-to-end download of a known-stable
# public video. Gauge values: 1 = last attempt succeeded, 0 = last attempt
# failed, -1 = no attempt yet this process. Paired with a "last success
# timestamp" gauge so alert rules can fire on staleness as well as red.
download_canary_status = Gauge(
    "scribe_download_canary_status",
    "Last yt-dlp download canary result (1=ok, 0=fail, -1=never run).",
)
download_canary_status.set(-1)

download_canary_last_success_timestamp = Gauge(
    "scribe_download_canary_last_success_timestamp_seconds",
    "Unix epoch of the most recent successful yt-dlp download canary run.",
)
download_canary_last_success_timestamp.set(-1)

download_canary_runs_total = Counter(
    "scribe_download_canary_runs_total",
    "yt-dlp download canary attempts, labelled by outcome (ok/fail).",
    labelnames=("outcome",),
)


# Outcome of the overall fallback chain run.
# outcome labels: success_first, success_after_fallback, all_failed.
summary_chain_outcome_total = Counter(
    "scribe_summary_chain_outcome_total",
    "Summary fallback-chain outcomes per run.",
    labelnames=("outcome",),
)

# Map-reduce summarization telemetry (#382). When the built prompt exceeds the
# configured threshold the chain summarises the transcript in chunks (map) and
# merges the partials (reduce) so payload-limited backends do not 413.
# result labels: success, truncated (reduce input had to be truncated), failed.
summary_map_reduce_total = Counter(
    "scribe_summary_map_reduce_total",
    "Map-reduce summarization runs, labelled by provider and outcome.",
    labelnames=("provider", "result"),
)

# Number of transcript chunks fanned out per map-reduce run.
summary_map_reduce_chunks = Histogram(
    "scribe_summary_map_reduce_chunks",
    "Transcript chunks per map-reduce summarization run.",
    buckets=(1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 64),
)

# Transcription-provider chain telemetry — mirrors the summary-provider
# metrics. The fallback chain (scribe.pipeline.transcribe_providers) tries each
# provider (vast → optional openai / local-whisper) in order; a per-provider
# circuit breaker short-circuits a provider that has failed often enough.
#
# result labels: success, usage_limit, unavailable, timeout, error, skipped_tripped.
transcribe_provider_calls_total = Counter(
    "scribe_transcribe_provider_calls_total",
    "Transcription provider invocations, labelled by provider and outcome.",
    labelnames=("provider", "result"),
)

# Per-provider transcription breaker state. 0=closed, 1=half_open, 2=tripped.
transcribe_provider_state = Gauge(
    "scribe_transcribe_provider_state",
    "Circuit-breaker state per transcription provider (0=closed, 1=half_open, 2=tripped).",
    labelnames=("provider",),
)

# Outcome of the overall transcription fallback-chain run.
# outcome labels: success_first, success_after_fallback, all_failed.
transcribe_chain_outcome_total = Counter(
    "scribe_transcribe_chain_outcome_total",
    "Transcription fallback-chain outcomes per run.",
    labelnames=("outcome",),
)

# Per-provider estimated transcription spend (USD). The Vast line mirrors
# scribe_vast_spend_usd_total; hosted providers (e.g. openai) get their own
# cost line here so spend caps and dashboards can see each backend separately.
transcribe_provider_spend_usd_total = Counter(
    "scribe_transcribe_provider_spend_usd_total",
    "Cumulative estimated transcription spend in USD, labelled by provider.",
    labelnames=("provider",),
)


def gauge_value(g: Gauge) -> float:
    """Read the current value of an unlabelled Gauge using prometheus_client's
    public `Collector.collect()` surface. We use this in place of touching the
    private `_value.get()` so that an upstream refactor of internals stays a
    one-line fix here rather than scattered access in every caller."""
    metric = next(iter(g.collect()), None)
    if metric is None or not metric.samples:
        return 0.0
    return float(metric.samples[0].value)


def export() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
