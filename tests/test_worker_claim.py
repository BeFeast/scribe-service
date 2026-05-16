"""DB-coupled worker queue claim tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from scribe.config import Settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.worker.loop import _claim_next_job, recover_interrupted_jobs

TEST_VIDEO_IDS = ("workerclaim1", "workerclaim2")
RECOVERY_VIDEO_IDS = (
    "recover-dl",
    "recover-tr",
    "recover-sum",
    "recover-queue",
    "recover-done",
    "recover-failed",
)


def test_settings_worker_concurrency_defaults_to_two(monkeypatch):
    monkeypatch.delenv("SCRIBE_WORKER_CONCURRENCY", raising=False)

    assert Settings(_env_file=None).worker_concurrency == 2


def test_two_workers_claim_distinct_jobs_concurrently(engine):
    """Two sessions claiming at the same time should skip locked rows."""
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    with SessionLocal() as session:
        session.execute(delete(Job).where(Job.video_id.in_(TEST_VIDEO_IDS)))
        session.commit()

    try:
        with SessionLocal() as session:
            session.add_all(
                [
                    Job(url="https://youtu.be/workerclaim1", video_id="workerclaim1", status=JobStatus.queued),
                    Job(url="https://youtu.be/workerclaim2", video_id="workerclaim2", status=JobStatus.queued),
                ]
            )
            session.commit()

        lock_session = SessionLocal()
        try:
            lock_session.scalars(
                select(Job)
                .where(Job.status == JobStatus.queued, Job.video_id.not_in(TEST_VIDEO_IDS))
                .with_for_update()
            ).all()

            barrier = Barrier(2)

            def claim_one() -> int | None:
                with SessionLocal() as session:
                    barrier.wait(timeout=5)
                    job = _claim_next_job(session)
                    return None if job is None else job.id

            with ThreadPoolExecutor(max_workers=2) as executor:
                claimed_ids = {result for result in executor.map(lambda _: claim_one(), range(2))}
        finally:
            lock_session.rollback()
            lock_session.close()

        assert None not in claimed_ids
        assert len(claimed_ids) == 2

        with SessionLocal() as session:
            rows = session.scalars(select(Job).where(Job.id.in_(claimed_ids))).all()
            assert {row.video_id for row in rows} == set(TEST_VIDEO_IDS)
            assert {row.status for row in rows} == {JobStatus.downloading}
            leftover = session.scalars(
                select(Job).where(
                    Job.video_id.in_(TEST_VIDEO_IDS),
                    Job.status == JobStatus.queued,
                )
            ).all()
            assert leftover == []
    finally:
        with SessionLocal() as session:
            session.execute(delete(Job).where(Job.video_id.in_(TEST_VIDEO_IDS)))
            session.commit()


def test_recover_interrupted_jobs_requeues_only_mid_stage_rows(db_session):
    db_session.execute(delete(Job).where(Job.video_id.in_(RECOVERY_VIDEO_IDS)))
    db_session.commit()

    jobs = [
        Job(url="https://youtu.be/recover-dl", video_id="recover-dl", status=JobStatus.downloading),
        Job(url="https://youtu.be/recover-tr", video_id="recover-tr", status=JobStatus.transcribing),
        Job(url="https://youtu.be/recover-sum", video_id="recover-sum", status=JobStatus.summarizing),
        Job(url="https://youtu.be/recover-queue", video_id="recover-queue", status=JobStatus.queued),
        Job(url="https://youtu.be/recover-done", video_id="recover-done", status=JobStatus.done),
        Job(url="https://youtu.be/recover-failed", video_id="recover-failed", status=JobStatus.failed),
    ]
    db_session.add_all(jobs)
    db_session.flush()
    db_session.add(
        Transcript(
            job_id=jobs[2].id,
            video_id="recover-sum",
            title="Recovered summary",
            transcript_md="Transcript body",
            summary_md=None,
            short_description=None,
        )
    )
    db_session.commit()

    try:
        assert recover_interrupted_jobs(db_session) == 3

        rows = {
            job.video_id: job
            for job in db_session.scalars(select(Job).where(Job.video_id.in_(RECOVERY_VIDEO_IDS))).all()
        }
        assert rows["recover-dl"].status == JobStatus.queued
        assert rows["recover-tr"].status == JobStatus.queued
        assert rows["recover-sum"].status == JobStatus.queued
        assert rows["recover-queue"].status == JobStatus.queued
        assert rows["recover-done"].status == JobStatus.done
        assert rows["recover-failed"].status == JobStatus.failed
        assert rows["recover-sum"].transcript is not None
        assert rows["recover-sum"].transcript.summary_md is None
    finally:
        db_session.execute(delete(Job).where(Job.video_id.in_(RECOVERY_VIDEO_IDS)))
        db_session.commit()
