"""Ops-dashboard helpers — queue depth, worker pool, backup heartbeat,
spend series, and a best-effort system rollcall.

These are the building blocks the future `/api/ops` endpoint composes; keeping
them here means routes.py stays a thin caller and individual pieces are
unit-testable without a FastAPI client.

The probes in `_system_rollcall` are best-effort. Each probe is responsible
for bounding its own I/O (urlopen `timeout=`, postgres `statement_timeout`),
so we don't need a wall-clock executor on top — that pattern would have
abandoned a stuck thread on every call and slowly leaked the worker pool.
A probe that raises is reported as `status: "warn"` with a short reason; the
rollcall never raises.
"""
from __future__ import annotations

import datetime as dt
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib import metadata as _md
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scribe.config import settings
from scribe.db.models import Job, JobStatus
from scribe.db.session import SessionLocal
from scribe.obs import metrics

# Statuses surfaced by the rollcall — alphabetised for readability.
Status = Literal["ok", "warn", "err"]

_ACTIVE_STATUSES = (
    JobStatus.queued,
    JobStatus.downloading,
    JobStatus.transcribing,
    JobStatus.summarizing,
)

_PROBE_TIMEOUT_S = 2.0
# Codex calls happening at least this often = `ok`; older than this = `warn`.
_CODEX_FRESH_SECONDS = 3600
# Same idea for Vast.ai launches — last successful whisper run.
_VAST_FRESH_SECONDS = 24 * 3600


def _queue_depth(session: Session) -> int:
    """Count of jobs in non-terminal states (queued/downloading/transcribing/summarizing)."""
    count = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(_ACTIVE_STATUSES))
    )
    return int(count or 0)


def _workers_busy() -> int:
    """Current value of the `scribe_workers_busy` gauge. The gauge is updated
    by `worker/loop.py::process_job` and is process-wide."""
    return int(metrics.gauge_value(metrics.workers_busy))


def _spend_series_14d(session: Session) -> list[float]:
    """Return exactly 14 floats: per-day Vast spend USD, oldest→newest, ending
    on today (UTC). Missing days are zero-padded so the series is always
    rectangular for the ops dashboard sparkline."""
    today = dt.datetime.now(dt.UTC).date()
    start = today - dt.timedelta(days=13)
    # `AT TIME ZONE 'UTC'` pins the daily bucket boundary to UTC midnight so the
    # series stays stable regardless of the connection's session TZ — otherwise
    # the bucket for the same `created_at` could slide a day between regions.
    rows = session.execute(
        text(
            """
            SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                   COALESCE(SUM(vast_cost), 0) AS spend
            FROM transcripts
            WHERE created_at >= :start
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"start": dt.datetime.combine(start, dt.time.min, tzinfo=dt.UTC)},
    ).all()
    by_day = {row[0]: float(row[1] or 0.0) for row in rows}
    return [round(by_day.get(start + dt.timedelta(days=i), 0.0), 4) for i in range(14)]


def _backup_heartbeat() -> dict:
    """Read the scribe-backups sidecar heartbeat file. Same shape and semantics
    as GET /admin/backup-status — exposed here so /api/ops can include it
    without a self-HTTP call."""
    path = Path(settings.backup_status_path)
    payload: dict = {
        "path": str(path),
        "last_success_ts": None,
        "last_success_iso": None,
        "age_seconds": None,
        "stale_after_seconds": settings.backup_stale_after_seconds,
        "stale": True,
    }
    try:
        ts = int(path.read_text().strip())
        now = int(time.time())
        age = max(0, now - ts)
        threshold = settings.backup_stale_after_seconds
        payload.update(
            last_success_ts=ts,
            last_success_iso=dt.datetime.fromtimestamp(ts, tz=dt.UTC).isoformat(timespec="seconds"),
            age_seconds=age,
            stale=bool(threshold) and age >= threshold,
        )
    except FileNotFoundError:
        payload["error"] = "no backup recorded yet"
    except (OSError, ValueError, OverflowError) as exc:
        payload["error"] = f"unreadable heartbeat: {exc}"
    return payload


# ---------------------------------------------------------------- rollcall
def _probe(label: str, fn: Callable[[], tuple[str, Status]]) -> dict:
    """Run `fn` and surface its (value, status). Any exception is converted
    to a `warn` entry with a short reason — the rollcall never raises and
    never blocks beyond what `fn` itself does. Each probe is responsible for
    bounding its own I/O (e.g. urlopen `timeout=`, postgres `statement_timeout`)."""
    try:
        value, status = fn()
    except Exception as exc:
        return {"label": label, "value": f"probe failed: {type(exc).__name__}: {exc}"[:200], "status": "warn"}
    return {"label": label, "value": value, "status": status}


def _probe_scribe_service() -> tuple[str, Status]:
    try:
        version = _md.version("scribe")
    except _md.PackageNotFoundError:
        version = "unknown"
    return f"v{version}", "ok"


def _probe_worker_pool() -> tuple[str, Status]:
    from scribe.worker import loop as worker_loop

    value = f"{settings.worker_concurrency} threads · loop tick {worker_loop.LOOP_TICK_MS}ms"
    threads = list(worker_loop.active_worker_threads)
    if not threads:
        # Not yet started (e.g., test harness without app lifespan). Surface as
        # `ok` with the configured roster — the rollcall is not a liveness probe
        # for the test process.
        return value, "ok"
    dead = [t for t in threads if not t.is_alive()]
    if dead:
        return f"{value} · {len(dead)}/{len(threads)} thread(s) dead", "err"
    return value, "ok"


def _probe_postgres() -> tuple[str, Status]:
    """Liveness + connection count for the configured database. Owns its own
    short-lived Session so we never share a request-scoped connection across
    threads; bounds itself with `statement_timeout` so a stuck DB can't hang
    the rollcall."""
    timeout_ms = int(_PROBE_TIMEOUT_S * 1000)
    with SessionLocal() as s:
        s.execute(text(f"SET statement_timeout = {timeout_ms}"))
        s.execute(text("SELECT 1")).scalar()
        conns = s.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        ).scalar()
    return f"ready · {int(conns or 0)} conn", "ok"


def _probe_vast() -> tuple[str, Status]:
    ts = float(metrics.gauge_value(metrics.last_vast_launch_timestamp))
    if ts <= 0:
        return "no recent launches recorded", "warn"
    age = time.time() - ts
    iso = dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).isoformat(timespec="seconds")
    status: Status = "ok" if age <= _VAST_FRESH_SECONDS else "warn"
    return f"last launch {iso} ({int(age)}s ago)", status


def _probe_chhoto() -> tuple[str, Status]:
    base = settings.shortlink_base.rstrip("/")
    if not base:
        return "shortlink_base not configured", "warn"
    req = urllib.request.Request(base, method="HEAD")
    # urlopen honours `timeout`; this bounds the probe's I/O on its own.
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    # Anything below 500 means the host is up enough to answer; only 5xx is
    # treated as a real fault. 4xx (e.g. 404 on `/`) still means Chhoto is alive.
    status: Status = "ok" if code < 500 else "err"
    return f"HTTP {code} @ {base}", status


def _probe_codex() -> tuple[str, Status]:
    ts = float(metrics.gauge_value(metrics.last_codex_success_timestamp))
    if ts <= 0:
        return "no recent summaries recorded", "warn"
    age = time.time() - ts
    iso = dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).isoformat(timespec="seconds")
    status: Status = "ok" if age <= _CODEX_FRESH_SECONDS else "warn"
    return f"last success {iso} ({int(age)}s ago)", status


def _system_rollcall() -> list[dict]:
    """Best-effort probe of each external dependency. Each entry is
    `{"label": str, "value": str, "status": "ok|warn|err"}`; probe failures
    degrade to `warn` rather than raising. The Postgres probe owns its own
    short-lived session; no request-scoped session is needed here."""
    return [
        _probe("scribe-service", _probe_scribe_service),
        _probe("Worker", _probe_worker_pool),
        _probe("Postgres", _probe_postgres),
        _probe("Vast.ai", _probe_vast),
        _probe("Chhoto shortlinks", _probe_chhoto),
        _probe("codex CLI", _probe_codex),
    ]
