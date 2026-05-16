"""Pydantic request/response shapes for the HTTP API."""
from __future__ import annotations

import datetime as dt

from pydantic import AnyHttpUrl, BaseModel


class JobCreate(BaseModel):
    url: str
    source: str | None = None
    # If set, scribe POSTs the JobView JSON here on terminal status. Best-
    # effort delivery — failures are logged + counted but don't fail the job.
    # AnyHttpUrl rejects malformed values at the API boundary (422) so the
    # worker never has to deal with `http:/typo` or similar at delivery time.
    callback_url: AnyHttpUrl | None = None


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


class PromptVersionView(BaseModel):
    id: str
    len_chars: int
    len_tokens_est: int
    first_line: str
    is_active: bool = False


class PromptListView(BaseModel):
    active_version: str
    versions: list[PromptVersionView]


class PromptWrite(BaseModel):
    body: str


class PromptActiveWrite(BaseModel):
    version: str


class PromptDryRunCreate(BaseModel):
    version: str
    transcript_id: int


class PromptDryRunView(BaseModel):
    version: str
    transcript_id: int
    summary_md: str
    tags: list[str]
