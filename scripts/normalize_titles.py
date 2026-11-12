"""Normalize existing titles and summary source links; dry-run unless --apply.

Run after alembic upgrade head, with SCRIBE_DATABASE_URL explicitly selecting
the intended database. Example: uv run python scripts/normalize_titles.py
No download, LLM call, or transcript body rewrite is performed.
"""
from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scribe.db.models import Job, Transcript
from scribe.db.titles import normalize_transcript_title
from scribe.pipeline.titles import normalize_title


def backfill(session: Session, *, apply: bool = False) -> int:
    changed = 0
    for transcript in session.scalars(select(Transcript).order_by(Transcript.id)):
        job = transcript.job
        before = (transcript.title, transcript.original_title, transcript.summary_md,
                  job.title, job.original_title)
        normalize_transcript_title(transcript, job)
        after = (transcript.title, transcript.original_title, transcript.summary_md,
                 job.title, job.original_title)
        if before != after:
            changed += 1
            print(f"transcript {transcript.id}: {before[0]!r} -> {transcript.title!r}"
                  f" (source/job metadata updated: {before[1:] != after[1:]})")
    # Also bound titles on queued/failed jobs that have no transcript yet.
    for job in session.scalars(select(Job).where(~Job.transcript.has()).order_by(Job.id)):
        if job.title is None:
            continue
        raw = job.original_title or job.title
        title, _ = normalize_title(raw)
        if title != job.title:
            changed += 1
            print(f"job {job.id}: {job.title!r} -> {title!r}")
            job.original_title = job.original_title or raw
            job.title = title
    if apply:
        session.commit()
    else:
        session.rollback()
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()
    url = os.environ.get("SCRIBE_DATABASE_URL")
    if not url:
        parser.error("Set SCRIBE_DATABASE_URL explicitly to select the target database")
    engine = create_engine(url)
    try:
        with Session(engine, autoflush=False) as session:
            count = backfill(session, apply=args.apply)
        print(f"{'Applied' if args.apply else 'Dry-run'}: {count} changed rows")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
