"""ORM models — jobs + transcripts (SQLAlchemy 2.0 style)."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    summarizing = "summarizing"
    done = "done"
    failed = "failed"


class AppConfig(Base):
    """Runtime config overlay. Values are stored as text and parsed by Settings."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional webhook target — scribe POSTs the final JobView JSON to
    # this URL on terminal status (done|failed). NULL = poll-only client.
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    transcript: Mapped[Transcript | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    stage_events: Mapped[list[JobStageEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStageEvent.started_at"
    )


class Transcript(Base):
    """The product of a completed job: transcript + summary + metadata. Source of truth.

    A transcript may be **partial** — `summary_md IS NULL` — after a successful
    whisper run whose summary step failed. Partial rows let the worker retry just
    the summary on the next attempt instead of re-running GPU transcription.
    `summary_md` flips to a non-NULL string once codex returns successfully.
    """

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    video_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_md: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = partial (whisper done, summary pending or failed).
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Estimated Vast.ai spend for this transcribe job in USD. Populated by
    # whisper_client.TranscribeResult.vast_cost; NULL when whisper ran
    # outside the metered path (warm pool, mock, etc).
    vast_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_shortlink: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_shortlink: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="transcript")


class JobStageEvent(Base):
    """Persisted lifecycle timing for one job pipeline stage."""

    __tablename__ = "job_stage_events"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_job_stage_events_job_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="stage_events")
