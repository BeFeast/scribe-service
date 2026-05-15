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

# Codex OAuth-token revocations detected in stderr. A spike here means the
# operator needs to re-login (`docker exec -it scribe codex login --device-auth`).
codex_token_revoked_total = Counter(
    "scribe_codex_token_revoked_total",
    "Times codex stderr matched an OAuth-token-revocation signature.",
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


def export() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
