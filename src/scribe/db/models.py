"""ORM models — jobs + transcripts (SQLAlchemy 2.0 style)."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    summarizing = "summarizing"
    done = "done"
    failed = "failed"


class Job(Base):
    """One video-summary request. Dedup is by video_id against completed transcripts."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    video_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"),
        nullable=False,
        default=JobStatus.queued,
        index=True,
    )
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    transcript: Mapped[Transcript | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class Transcript(Base):
    """The product of a completed job: transcript + summary + metadata. Source of truth."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    video_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_md: Mapped[str] = mapped_column(Text, nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_shortlink: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_shortlink: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="transcript")
