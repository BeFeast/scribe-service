"""Regression tests for the partial-transcript resume race (scr-549 / #353).

Two workers can resume the *same* partial transcript (whisper done, summary
pending). ``_find_partial_transcript()`` takes no row lock, and a ``FOR
UPDATE`` on the lookup would not help because the job's ``summarizing``
transition commits — releasing the lock — before the summary is written. A
blind ``UPDATE`` therefore let the slower worker overwrite a summary the
faster worker had already committed (silent data loss).

The fix makes the summary write a compare-and-set guarded on
``summary_md IS NULL``. These tests interleave two workers on one partial and
assert the completed summary is never clobbered.

DB-coupled: skipped unless ``SCRIBE_TEST_DATABASE_URL`` is set (CI provides a
Postgres service). Postgres row-locking is what serializes the CAS.
"""
from __future__ import annotations

import threading

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.summary_validator import SummaryResult
from scribe.worker import loop as worker_loop

VIDEO_ID = "resume-race-vid"
SUMMARY_A = "## Summary\n\nWINNER-SUMMARY-A body marker.\n"
SUMMARY_B = "## Summary\n\nLOSER-SUMMARY-B body marker.\n"


def _patch_summarizer_by_thread(monkeypatch) -> None:
    """Each worker thread returns a distinct summary so we can tell which
    write survived. The injected summary is returned verbatim (the partial has
    no author metadata, so frontmatter injection is a pass-through)."""

    def fake_summarize(_transcript_md, title=None):  # noqa: ARG001
        if threading.current_thread().name == "resume-worker-a":
            return SummaryResult(summary_md=SUMMARY_A, tags=["a"], short_description="A")
        return SummaryResult(summary_md=SUMMARY_B, tags=["b"], short_description="B")

    monkeypatch.setattr(worker_loop.summarizer, "summarize", fake_summarize)


def _seed_partial(SessionLocal) -> tuple[int, int]:
    """Insert one partial transcript plus two jobs (A, B) resuming it."""
    with SessionLocal() as session:
        session.execute(delete(Transcript).where(Transcript.video_id == VIDEO_ID))
        session.execute(delete(Job).where(Job.video_id == VIDEO_ID))
        session.commit()

        origin = Job(url=f"https://youtu.be/{VIDEO_ID}", video_id=VIDEO_ID, status=JobStatus.failed)
        job_a = Job(url=f"https://youtu.be/{VIDEO_ID}", video_id=VIDEO_ID, status=JobStatus.downloading)
        job_b = Job(url=f"https://youtu.be/{VIDEO_ID}", video_id=VIDEO_ID, status=JobStatus.downloading)
        session.add_all([origin, job_a, job_b])
        session.flush()
        session.add(
            Transcript(
                job_id=origin.id,
                video_id=VIDEO_ID,
                title="Resume race video",
                transcript_md="Transcript body for the resume-race regression.",
                summary_md=None,
            )
        )
        session.commit()
        return job_a.id, job_b.id


def _cleanup(SessionLocal) -> None:
    with SessionLocal() as session:
        session.execute(delete(Transcript).where(Transcript.video_id == VIDEO_ID))
        session.execute(delete(Job).where(Job.video_id == VIDEO_ID))
        session.commit()


def _run_threads(targets: dict[str, callable]) -> None:
    """Run named targets concurrently, propagating any in-thread exception."""
    errors: list[BaseException] = []

    def guard(fn):
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            errors.append(exc)

    threads = [threading.Thread(target=guard, args=(fn,), name=name) for name, fn in targets.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "worker thread did not finish — likely deadlocked on the row lock"
    if errors:
        raise errors[0]


def test_partial_resume_does_not_clobber_completed_summary(engine, monkeypatch):
    """Worker B looks up the partial while it is still NULL, then worker A
    finishes and commits its summary. B's write must be rejected by the CAS,
    so A's completed summary survives."""
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    _patch_summarizer_by_thread(monkeypatch)
    job_a_id, job_b_id = _seed_partial(SessionLocal)

    b_looked_up = threading.Event()
    a_committed = threading.Event()

    def run_a() -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_a_id)
            partial = worker_loop._find_partial_transcript(session, VIDEO_ID)
            assert partial is not None
            # Hold until B has also resolved the still-partial row, so both
            # workers genuinely raced the same NULL-summary transcript.
            assert b_looked_up.wait(timeout=10)
            worker_loop._summarize_and_finalize(session, job, partial, partial.title, promoted=True)
            a_committed.set()

    def run_b() -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_b_id)
            partial = worker_loop._find_partial_transcript(session, VIDEO_ID)
            assert partial is not None
            b_looked_up.set()
            # Only write after A has fully committed its summary.
            assert a_committed.wait(timeout=10)
            worker_loop._summarize_and_finalize(session, job, partial, partial.title, promoted=True)

    try:
        _run_threads({"resume-worker-a": run_a, "resume-worker-b": run_b})

        with SessionLocal() as session:
            rows = session.scalars(select(Transcript).where(Transcript.video_id == VIDEO_ID)).all()
            assert len(rows) == 1, "resume must reuse the single partial, not fork a new transcript"
            transcript = rows[0]
            assert transcript.summary_md is not None
            assert SUMMARY_A.strip() in transcript.summary_md
            assert "LOSER-SUMMARY-B" not in transcript.summary_md
            assert transcript.short_description == "A"

            jobs = {
                job.id: job
                for job in session.scalars(select(Job).where(Job.id.in_([job_a_id, job_b_id]))).all()
            }
            assert jobs[job_a_id].status == JobStatus.done
            assert jobs[job_b_id].status == JobStatus.done
    finally:
        _cleanup(SessionLocal)


def test_concurrent_partial_resume_keeps_single_summary(engine, monkeypatch):
    """Both workers hit the summary write concurrently. Postgres row-locking
    serializes the CAS: exactly one summary wins, the other is rejected, and
    no worker leaves the row partial or forks a duplicate."""
    SessionLocal = sessionmaker(engine, autoflush=False, autocommit=False, future=True)
    _patch_summarizer_by_thread(monkeypatch)
    job_a_id, job_b_id = _seed_partial(SessionLocal)

    barrier = threading.Barrier(2, timeout=10)

    def resume(job_id: int):
        def _run() -> None:
            with SessionLocal() as session:
                job = session.get(Job, job_id)
                partial = worker_loop._find_partial_transcript(session, VIDEO_ID)
                assert partial is not None
                # Release both workers into the summarize+CAS path together.
                barrier.wait()
                worker_loop._summarize_and_finalize(session, job, partial, partial.title, promoted=True)

        return _run

    try:
        _run_threads({"resume-worker-a": resume(job_a_id), "resume-worker-b": resume(job_b_id)})

        with SessionLocal() as session:
            rows = session.scalars(select(Transcript).where(Transcript.video_id == VIDEO_ID)).all()
            assert len(rows) == 1
            summary_md = rows[0].summary_md
            assert summary_md is not None, "neither worker may leave the summary NULL"
            survivors = [m for m in ("WINNER-SUMMARY-A", "LOSER-SUMMARY-B") if m in summary_md]
            assert len(survivors) == 1, f"exactly one summary must survive, got {survivors}"

            jobs = session.scalars(select(Job).where(Job.id.in_([job_a_id, job_b_id]))).all()
            assert {job.status for job in jobs} == {JobStatus.done}
    finally:
        _cleanup(SessionLocal)
