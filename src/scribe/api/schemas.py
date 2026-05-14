"""Pydantic request/response shapes for the HTTP API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class JobCreate(BaseModel):
    url: str
    source: str | None = None


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
    transcript: TranscriptBrief | None = None
