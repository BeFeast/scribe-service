"""Regression: a partial-transcript resume must not clobber a summary that a
concurrent worker completed first (scr-549 / #353).

``_find_partial_transcript`` selects the most recent partial with no row lock,
so two jobs for the same video_id can both pick up the same partial. If one
worker finishes the summary while the other is still summarizing, the slower
worker used to write its (older) result back over the finished summary — silent
data loss. ``_summarize_and_finalize`` now re-reads the row under
``SELECT ... FOR UPDATE`` and aborts the write when ``summary_md`` is already set.

This test interleaves two workers on the same partial and asserts the completed
summary survives. It needs SCRIBE_TEST_DATABASE_URL (Postgres `FOR UPDATE`), so
it is skipped in the pure-function local run and exercised in CI.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline import summarizer as summarizer_mod
from scribe.pipeline.summary_validator import SummaryResult
from scribe.worker import loop as worker_loop

RACE_VIDEO_ID = "resume-race-353"


def _fixed_summary(text: str):
    return lambda *args, **kwargs: SummaryResult(summary_md=text, tags=["topic"], short_description="d")


def test_concurrent_resume_does_not_clobber_completed_summary(engine, monkeypatch):
    # expire_on_commit=False mirrors the production SessionLocal — a worker keeps
    # its (now stale) in-memory view of the partial across its own commits, which
    # is exactly the condition the clobber depends on.
    SessionLocal = sessionmaker(engine, autoflush=False, expire_on_commit=False, future=True)

    with SessionLocal() as setup:
        setup.execute(delete(Transcript).where(Transcript.video_id == RACE_VIDEO_ID))
        setup.execute(delete(Job).where(Job.video_id == RACE_VIDEO_ID))
        setup.commit()

        job_a = Job(url=f"https://youtu.be/{RACE_VIDEO_ID}", video_id=RACE_VIDEO_ID, status=JobStatus.downloading)
        job_b = Job(url=f"https://youtu.be/{RACE_VIDEO_ID}", video_id=RACE_VIDEO_ID, status=JobStatus.downloading)
        setup.add_all([job_a, job_b])
        setup.flush()
        transcript = Transcript(
            job_id=job_a.id,
            video_id=RACE_VIDEO_ID,
            title="Race title",
            transcript_md="transcript body",
            summary_md=None,
        )
        setup.add(transcript)
        setup.commit()
        job_a_id, job_b_id, transcript_id = job_a.id, job_b.id, transcript.id

    try:
        # Worker B opens its session and loads the partial FIRST, capturing the
        # stale summary_md=NULL view the data-loss race depends on. It then runs
        # its (slow) summary while worker A finishes.
        session_b = SessionLocal()
        job_b_row = session_b.get(Job, job_b_id)
        partial_b = session_b.get(Transcript, transcript_id)
        assert partial_b.summary_md is None

        # Worker A resumes the same partial and completes its summary first.
        monkeypatch.setattr(summarizer_mod, "summarize", _fixed_summary("SUMMARY-A"))
        with SessionLocal() as session_a:
            job_a_row = session_a.get(Job, job_a_id)
            partial_a = session_a.get(Transcript, transcript_id)
            worker_loop._summarize_and_finalize(
                session_a, job_a_row, partial_a, partial_a.title, promoted=True
            )

        with SessionLocal() as check:
            assert check.get(Transcript, transcript_id).summary_md == "SUMMARY-A"

        # Worker B now finishes its summary and tries to write back through its
        # stale partial view. The compare-and-set must keep worker A's summary.
        monkeypatch.setattr(summarizer_mod, "summarize", _fixed_summary("SUMMARY-B"))
        worker_loop._summarize_and_finalize(
            session_b, job_b_row, partial_b, partial_b.title, promoted=True
        )
        session_b.close()

        with SessionLocal() as verify:
            final = verify.get(Transcript, transcript_id)
            assert final.summary_md == "SUMMARY-A"
            assert "SUMMARY-B" not in final.summary_md
            # Both jobs still reach done: worker B adopts the completed transcript.
            assert verify.get(Job, job_a_id).status == JobStatus.done
            assert verify.get(Job, job_b_id).status == JobStatus.done
    finally:
        with SessionLocal() as cleanup:
            cleanup.execute(delete(Transcript).where(Transcript.video_id == RACE_VIDEO_ID))
            cleanup.execute(delete(Job).where(Job.video_id == RACE_VIDEO_ID))
            cleanup.commit()
