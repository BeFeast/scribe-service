"""Shared title write-back for worker, resummarize, and explicit backfills."""
from __future__ import annotations

from scribe.db.models import Job, Transcript
from scribe.pipeline.titles import normalize_title, summary_heading, summary_source


def normalize_transcript_title(transcript: Transcript, job: Job | None = None) -> None:
    raw = transcript.original_title or transcript.title
    # The preserved raw value makes the long-title flag reproducible on retries
    # without adding separate persisted state that could drift out of sync.
    normalized, was_long = normalize_title(raw, author=transcript.author_name)
    title = (summary_heading(transcript.summary_md) if was_long else None) or normalized
    if raw != title and transcript.original_title is None:
        transcript.original_title = raw
    transcript.title = title
    transcript.summary_md = summary_source(transcript.summary_md, title)
    if job is not None:
        if job.original_title is None and raw != title:
            job.original_title = raw
        job.title = title
