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


def export() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
