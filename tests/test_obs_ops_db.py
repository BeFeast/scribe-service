"""DB-coupled tests for the ops helpers — queue depth + 14-day spend series.

Skipped unless SCRIBE_TEST_DATABASE_URL is set (same convention as the other
DB tests). The `db_session` fixture rolls back per-test, so seeding doesn't
need explicit cleanup.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete

from scribe.db.models import Job, JobStatus, Transcript
from scribe.obs import ops


def test_queue_depth_counts_only_non_terminal(db_session):
    """`_queue_depth` must include queued/downloading/transcribing/summarizing
    and ignore done/failed — those are the active states the ops dashboard
    treats as 'queue' work."""
    db_session.execute(delete(Job).where(Job.video_id.like("opsqd%")))
    db_session.commit()

    db_session.add_all([
        Job(url="https://youtu.be/opsqd1", video_id="opsqd1", status=JobStatus.queued),
        Job(url="https://youtu.be/opsqd2", video_id="opsqd2", status=JobStatus.downloading),
        Job(url="https://youtu.be/opsqd3", video_id="opsqd3", status=JobStatus.transcribing),
        Job(url="https://youtu.be/opsqd4", video_id="opsqd4", status=JobStatus.summarizing),
        Job(url="https://youtu.be/opsqd5", video_id="opsqd5", status=JobStatus.done),
        Job(url="https://youtu.be/opsqd6", video_id="opsqd6", status=JobStatus.failed),
    ])
    db_session.commit()

    depth = ops._queue_depth(db_session)
    # Includes the 4 non-terminal rows we seeded. Don't assert equality on the
    # absolute count — other tests may share the DB. Assert relative.
    db_session.execute(delete(Job).where(Job.video_id.like("opsqd%")))
    db_session.commit()
    after_clean = ops._queue_depth(db_session)
    assert depth - after_clean == 4


def test_spend_series_14d_returns_exactly_14_floats(db_session):
    """Empty (or near-empty) DB still produces a rectangular 14-element series."""
    series = ops._spend_series_14d(db_session)
    assert isinstance(series, list)
    assert len(series) == 14
    assert all(isinstance(value, float) for value in series)


def test_spend_series_14d_buckets_by_day_and_zero_pads(db_session):
    """Seeded transcripts within the 14-day window land in the right bucket;
    days with no transcripts are 0.0."""
    db_session.execute(delete(Transcript).where(Transcript.video_id.like("opsspend%")))
    db_session.execute(delete(Job).where(Job.video_id.like("opsspend%")))
    db_session.commit()

    today = dt.datetime.now(dt.UTC).date()

    def _seed(video_id: str, days_ago: int, cost: float) -> None:
        job = Job(url=f"https://youtu.be/{video_id}", video_id=video_id, status=JobStatus.done)
        db_session.add(job)
        db_session.flush()
        ts = dt.datetime.combine(
            today - dt.timedelta(days=days_ago), dt.time(12, 0), tzinfo=dt.UTC
        )
        db_session.add(Transcript(
            job_id=job.id, video_id=video_id, title=video_id,
            transcript_md="t", summary_md="s",
            vast_cost=cost, created_at=ts,
        ))

    # Two transcripts on the same day should sum; one mid-window; one out-of-window.
    _seed("opsspend01", 0, 0.10)
    _seed("opsspend02", 0, 0.05)
    _seed("opsspend03", 7, 0.42)
    _seed("opsspend04", 20, 99.0)  # outside the 14-day window — must be excluded
    db_session.commit()

    try:
        series = ops._spend_series_14d(db_session)
        assert len(series) == 14
        # series is oldest→newest; today is at index 13.
        assert series[13] == 0.15
        assert series[6] == 0.42
        # Untouched days are zero.
        empty_days = [v for i, v in enumerate(series) if i not in {6, 13}]
        assert all(v == 0.0 for v in empty_days)
    finally:
        db_session.execute(delete(Transcript).where(Transcript.video_id.like("opsspend%")))
        db_session.execute(delete(Job).where(Job.video_id.like("opsspend%")))
        db_session.commit()


def test_probe_postgres_returns_ok_with_conn_count(engine, monkeypatch):
    """Live SELECT 1 + pg_stat_activity probe — must return ok + a conn count.

    `_probe_postgres` opens its own short-lived session via the module-level
    `SessionLocal`. We point that at the test engine so the probe runs against
    SCRIBE_TEST_DATABASE_URL rather than the production URL."""
    from sqlalchemy.orm import sessionmaker

    test_factory = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(ops, "SessionLocal", test_factory)

    value, status = ops._probe_postgres()
    assert status == "ok"
    assert "ready" in value
    assert "conn" in value
