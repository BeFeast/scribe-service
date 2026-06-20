"""Per-job pipeline toggles (#296): summarize gate + summary_prompt override.

These exercise ``_summarize_and_finalize`` against a persisted partial
transcript, so they need SCRIBE_TEST_DATABASE_URL (Postgres ``FOR UPDATE`` and
ARRAY[Text]). Skipped in the pure-function local run, exercised in CI.

notify gating is covered as a pure unit test in test_worker_webhook.py.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.summary_validator import SummaryResult
from scribe.worker import loop as worker_loop

SKIP_VIDEO_ID = "opt-skip-296"
PROMPT_VIDEO_ID = "opt-prompt-296"


def _reset(session, video_id: str) -> None:
    session.execute(delete(Transcript).where(Transcript.video_id == video_id))
    session.execute(delete(Job).where(Job.video_id == video_id))
    session.commit()


def test_summarize_false_skips_summary_and_marks_done(engine, monkeypatch):
    """summarize=false must finish the job without invoking the summarizer; the
    transcript stays partial (summary_md=NULL) and the job lands on done."""
    SessionLocal = sessionmaker(engine, autoflush=False, expire_on_commit=False, future=True)

    def must_not_run(*args, **kwargs):
        raise AssertionError("summarizer.summarize must not run when summarize=False")

    monkeypatch.setattr(worker_loop.summarizer, "summarize", must_not_run)

    with SessionLocal() as setup:
        _reset(setup, SKIP_VIDEO_ID)
        job = Job(
            url=f"https://youtu.be/{SKIP_VIDEO_ID}",
            video_id=SKIP_VIDEO_ID,
            status=JobStatus.transcribing,
            summarize=False,
        )
        setup.add(job)
        setup.flush()
        transcript = Transcript(
            job_id=job.id,
            video_id=SKIP_VIDEO_ID,
            title="Skip title",
            transcript_md="transcript body",
            summary_md=None,
        )
        setup.add(transcript)
        setup.commit()
        job_id, transcript_id = job.id, transcript.id

    with SessionLocal() as worker:
        job = worker.get(Job, job_id)
        transcript = worker.get(Transcript, transcript_id)
        worker_loop._summarize_and_finalize(worker, job, transcript, "Skip title", promoted=False)

    with SessionLocal() as check:
        assert check.get(Job, job_id).status == JobStatus.done
        assert check.get(Transcript, transcript_id).summary_md is None


def test_summary_prompt_forwarded_to_summarizer(engine, monkeypatch):
    """summary_prompt is passed to summarizer.summarize as prompt_body so the
    job overrides the active template; the resulting summary is persisted."""
    SessionLocal = sessionmaker(engine, autoflush=False, expire_on_commit=False, future=True)
    captured: dict[str, object] = {}

    def fake_summarize(transcript_md, *, title, prompt_body=None, **kwargs):
        captured["prompt_body"] = prompt_body
        captured["title"] = title
        return SummaryResult(summary_md="## TL;DR\n- point", tags=["topic"], short_description="d")

    monkeypatch.setattr(worker_loop.summarizer, "summarize", fake_summarize)

    with SessionLocal() as setup:
        _reset(setup, PROMPT_VIDEO_ID)
        job = Job(
            url=f"https://youtu.be/{PROMPT_VIDEO_ID}",
            video_id=PROMPT_VIDEO_ID,
            status=JobStatus.transcribing,
            summarize=True,
            summary_prompt="Only a one-line gist.",
        )
        setup.add(job)
        setup.flush()
        transcript = Transcript(
            job_id=job.id,
            video_id=PROMPT_VIDEO_ID,
            title="Prompt title",
            transcript_md="transcript body",
            summary_md=None,
        )
        setup.add(transcript)
        setup.commit()
        job_id, transcript_id = job.id, transcript.id

    with SessionLocal() as worker:
        job = worker.get(Job, job_id)
        transcript = worker.get(Transcript, transcript_id)
        worker_loop._summarize_and_finalize(worker, job, transcript, "Prompt title", promoted=False)

    assert captured["prompt_body"] == "Only a one-line gist."
    with SessionLocal() as check:
        assert check.get(Job, job_id).status == JobStatus.done
        assert check.get(Transcript, transcript_id).summary_md is not None
