"""Pydantic request/response shapes for the HTTP API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class JobCreate(BaseModel):
    url: str
    source: str | None = None
    # If set, scribe POSTs the JobView JSON here on terminal status. Best-
    # effort delivery — failures are logged + counted but don't fail the job.
    callback_url: str | None = None


class TranscriptBrief(BaseModel):
    id: int
    video_id: str
    title: str
    tags: list[str] | None = None
    duration_seconds: int | None = None
    lang: str | None = None
    summary_shortlink: str | None = None
    transcript_shortlink: str | None = None
    created_at: dt.datetime


class TranscriptFull(TranscriptBrief):
    job_id: int
    transcript_md: str
    summary_md: str


class JobView(BaseModel):
    job_id: int
    url: str
    video_id: str
    status: str
    error: str | None = None
    deduplicated: bool = False
    callback_url: str | None = None
    transcript: TranscriptBrief | None = None
