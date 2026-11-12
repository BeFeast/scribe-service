"""Backfill defaults must be read-only and repeated writes must be idempotent."""
import runpy
from pathlib import Path

from scribe.db.models import Job, JobStatus, Transcript

backfill = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "normalize_titles.py"))["backfill"]


def test_backfill_dry_run_apply_and_repeat(db_session, capsys):
    raw = "A huge legacy caption " * 70
    job = Job(url="https://facebook.com/reel/backfill", video_id="titles:backfill", title=raw, status=JobStatus.done)
    orphan = Job(url="https://x.com/status/backfill", video_id="titles:orphan", title=raw, status=JobStatus.failed)
    db_session.add_all([job, orphan])
    db_session.flush()
    transcript = Transcript(job_id=job.id, video_id=job.video_id, title=raw, transcript_md="body",
                            summary_md='---\nsource: "[[huge]]"\n---\n## Shared memory')
    db_session.add(transcript)
    db_session.commit()
    assert backfill(db_session) >= 2
    assert "-> 'Shared memory'" in capsys.readouterr().out
    db_session.expire_all()
    assert transcript.title == job.title == orphan.title == raw
    assert transcript.original_title is None
    backfill(db_session, apply=True)
    db_session.expire_all()
    assert transcript.title == job.title == "Shared memory"
    assert transcript.original_title == job.original_title == orphan.original_title == raw
    assert len(orphan.title) <= 100
    assert 'source: "[[shared-memory]]"' in transcript.summary_md
    assert backfill(db_session, apply=True) == 0
