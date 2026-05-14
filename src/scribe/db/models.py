"""ORM models. TODO(task#2): jobs + transcripts tables."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# TODO(task#2): class Job(Base)        — id, url, video_id, status, source, error, timestamps
# TODO(task#2): class Transcript(Base) — job_id, title, transcript_md, summary_md,
#                                        tags, duration, lang, shortlinks, created_at
