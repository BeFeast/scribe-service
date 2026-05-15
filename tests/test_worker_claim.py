"""DB-coupled worker queue claim tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from scribe.config import Settings
from scribe.db.models import Job, JobStatus
from scribe.worker.loop import _claim_next_job


def test_settings_worker_concurrency_defaults_to_two(monkeypatch):
    monkeypatch.delenv("SCRIBE_WORKER_CONCURRENCY", raising=False)

    assert Settings(_env_file=None).worker_concurrency == 2


def test_two_workers_claim_distinct_jobs_concurrently(engine):
    """Two sessions claiming at the same time should skip locked rows."""
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    with SessionLocal() as session:
        session.add_all(
            [
                Job(url="https://youtu.be/workerclaim1", video_id="workerclaim1", status=JobStatus.queued),
                Job(url="https://youtu.be/workerclaim2", video_id="workerclaim2", status=JobStatus.queued),
            ]
        )
        session.commit()

    barrier = Barrier(2)

    def claim_one() -> int | None:
        with SessionLocal() as session:
            barrier.wait(timeout=5)
            job = _claim_next_job(session)
            return None if job is None else job.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = {result for result in executor.map(lambda _: claim_one(), range(2))}

    assert None not in claimed_ids
    assert len(claimed_ids) == 2

    with SessionLocal() as session:
        rows = session.scalars(select(Job).where(Job.id.in_(claimed_ids))).all()
        assert {row.status for row in rows} == {JobStatus.downloading}
        assert _claim_next_job(session) is None
