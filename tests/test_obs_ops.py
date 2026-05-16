"""Tests for the ops-dashboard helpers (scribe.obs.ops).

The pure-Python pieces (workers_busy gauge plumbing, system rollcall probe
shape, backup heartbeat file parsing, codex/vast freshness probes) are
exercised without a database. The DB-backed pieces (queue depth + 14-day
spend series) live in `tests/test_obs_ops_db.py` so the rest of the suite
keeps running without SCRIBE_TEST_DATABASE_URL.
"""
from __future__ import annotations

import time

from scribe.obs import metrics, ops


def test_workers_busy_inc_dec_balance_via_helper():
    """The ops helper must reflect process-wide inc/dec from worker/loop."""
    start = ops._workers_busy()
    metrics.workers_busy.inc()
    metrics.workers_busy.inc()
    assert ops._workers_busy() == start + 2
    metrics.workers_busy.dec()
    metrics.workers_busy.dec()
    assert ops._workers_busy() == start


def test_process_job_increments_and_decrements_workers_busy(monkeypatch):
    """`process_job` must inc on entry and dec on exit even when the pipeline
    raises — the gauge would otherwise drift upward over the process lifetime."""
    from scribe.db.models import Job, JobStatus
    from scribe.worker import loop as worker_loop

    # No DB: stub the session and helpers the function touches before failing.
    class _Session:
        def get(self, *_args, **_kwargs): return None
        def rollback(self): pass
        def commit(self): pass

    def _boom(*_args, **_kwargs):
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(worker_loop, "_find_partial_transcript", _boom)
    monkeypatch.setattr(worker_loop, "_deliver_webhook", lambda *_a, **_k: None)

    job = Job(id=1, url="https://youtu.be/x", video_id="x", status=JobStatus.downloading)
    before = ops._workers_busy()
    # process_job catches its own exceptions and never re-raises — the failure
    # path through `_find_partial_transcript` still has to land in `finally`.
    worker_loop.process_job(_Session(), job)
    assert ops._workers_busy() == before, "workers_busy must be balanced after process_job"


def test_probe_swallows_exceptions_and_returns_warn():
    def explode():
        raise RuntimeError("kaboom")
    result = ops._probe("under-test", explode)
    assert result["label"] == "under-test"
    assert result["status"] == "warn"
    assert "RuntimeError" in result["value"]


def test_probe_times_out_at_budget():
    def slow():
        time.sleep(5)
        return "never", "ok"
    start = time.monotonic()
    result = ops._probe("slow-thing", slow, timeout_s=0.2)
    elapsed = time.monotonic() - start
    assert result["status"] == "warn"
    assert "timeout" in result["value"]
    # Should return promptly — well under the slow function's 5 s sleep.
    assert elapsed < 1.5


def test_probe_scribe_service_returns_ok_with_version():
    value, status = ops._probe_scribe_service()
    assert status == "ok"
    assert value.startswith("v")


def test_probe_worker_pool_reports_concurrency_and_tick(monkeypatch):
    # No threads registered (test process): probe surfaces config but flags ok.
    from scribe.worker import loop as worker_loop
    monkeypatch.setattr(worker_loop, "active_worker_threads", [])
    value, status = ops._probe_worker_pool()
    assert status == "ok"
    assert "threads · loop tick" in value
    assert f"{worker_loop.LOOP_TICK_MS}ms" in value


def test_probe_worker_pool_flags_dead_threads(monkeypatch):
    """A registered thread that's no longer alive must trip status=err."""
    from scribe.worker import loop as worker_loop

    class _DeadThread:
        def is_alive(self): return False

    monkeypatch.setattr(worker_loop, "active_worker_threads", [_DeadThread()])
    value, status = ops._probe_worker_pool()
    assert status == "err"
    assert "dead" in value


def test_probe_vast_warns_when_never_launched(monkeypatch):
    monkeypatch.setattr(metrics.last_vast_launch_timestamp, "set", metrics.last_vast_launch_timestamp.set)
    metrics.last_vast_launch_timestamp.set(-1)
    value, status = ops._probe_vast()
    assert status == "warn"
    assert "no recent" in value


def test_probe_vast_ok_when_fresh():
    metrics.last_vast_launch_timestamp.set(time.time())
    value, status = ops._probe_vast()
    assert status == "ok"
    assert "last launch" in value


def test_probe_vast_warns_when_stale():
    # 2 days ago — well past the 24h freshness window.
    metrics.last_vast_launch_timestamp.set(time.time() - 2 * 86400)
    _, status = ops._probe_vast()
    assert status == "warn"


def test_probe_codex_warns_when_never_run():
    metrics.last_codex_success_timestamp.set(-1)
    value, status = ops._probe_codex()
    assert status == "warn"
    assert "no recent" in value


def test_probe_codex_ok_when_fresh():
    metrics.last_codex_success_timestamp.set(time.time())
    _, status = ops._probe_codex()
    assert status == "ok"


def test_probe_codex_warns_after_one_hour():
    metrics.last_codex_success_timestamp.set(time.time() - 3601)
    _, status = ops._probe_codex()
    assert status == "warn"


def test_backup_heartbeat_missing_file(tmp_path, monkeypatch):
    from scribe.config import settings
    monkeypatch.setattr(settings, "backup_status_path", str(tmp_path / "_missing"))
    monkeypatch.setattr(settings, "backup_stale_after_seconds", 90_000)
    payload = ops._backup_heartbeat()
    assert payload["stale"] is True
    assert payload["last_success_ts"] is None
    assert "error" in payload


def test_backup_heartbeat_fresh(tmp_path, monkeypatch):
    from scribe.config import settings
    path = tmp_path / "_last_success_ts"
    now = int(time.time())
    path.write_text(f"{now}\n")
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "backup_stale_after_seconds", 90_000)
    payload = ops._backup_heartbeat()
    assert payload["stale"] is False
    assert payload["last_success_ts"] == now
    assert payload["age_seconds"] < 5


def test_system_rollcall_returns_six_entries():
    """The rollcall must include the six labels documented in the issue."""
    # Use a stubbed session — the Postgres probe is the only one that touches it.
    class _Session:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("no DB in this unit test")
    rollcall = ops._system_rollcall(_Session())
    labels = [item["label"] for item in rollcall]
    assert labels == [
        "scribe-service",
        "Worker",
        "Postgres",
        "Vast.ai",
        "Chhoto shortlinks",
        "codex CLI",
    ]
    # Postgres probe degrades to warn because the stub session raises.
    pg = next(item for item in rollcall if item["label"] == "Postgres")
    assert pg["status"] == "warn"
    assert "probe failed" in pg["value"]
    # Every entry has the right shape regardless of status.
    for item in rollcall:
        assert set(item) == {"label", "value", "status"}
        assert item["status"] in {"ok", "warn", "err"}
