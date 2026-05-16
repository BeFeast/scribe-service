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
    summary_md: str | None
    vast_cost: float | None = None


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


class LibraryRow(BaseModel):
    id: int
    video_id: str
    title: str
    tags: list[str] | None = None
    lang: str | None = None
    duration_seconds: int | None = None
    vast_cost: float | None = None
    created_at: dt.datetime
    summary_shortlink: str | None = None
    transcript_shortlink: str | None = None
    summary_excerpt: str
    is_partial: bool


class LibraryResponse(BaseModel):
    rows: list[LibraryRow]
    total: int
    limit: int
    offset: int


class JobStageView(BaseModel):
    state: str
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_s: int | None = None
    progress: float | None = None
    note: str | None = None


class ActiveJobView(BaseModel):
    id: int
    video_id: str
    url: str
    title: str | None = None
    status: str
    source: str | None = None
    started_at: dt.datetime
    elapsed_s: int
    stages: dict[str, JobStageView]


class ActiveJobsResponse(BaseModel):
    jobs: list[ActiveJobView]


class BackupSnapshot(BaseModel):
    last_success_iso: str | None = None
    age_seconds: int | None = None
    stale_after: int
    stale: bool
    path: str


class WorkerPoolSnapshot(BaseModel):
    active: int
    total: int


class SystemSnapshot(BaseModel):
    label: str
    value: str
    status: str


class RecentFailureSnapshot(BaseModel):
    id: int
    video_id: str
    url: str
    error: str | None = None
    updated_at: dt.datetime


class OpsSnapshot(BaseModel):
    window_days: int
    jobs_by_status: dict[str, int]
    transcripts_done: int
    transcripts_partial: int
    queue_depth: int
    vast_spend_24h: float
    vast_spend_7d: float
    vast_spend_30d: float
    daily_spend_cap_usd: float
    spend_series_14d: list[float]
    backup: BackupSnapshot
    worker_pool: WorkerPoolSnapshot
    recent_failures: list[RecentFailureSnapshot]
    system: list[SystemSnapshot]


class ConfigEntry(BaseModel):
    value: bool | float | int | str
    source: str
    mutable: bool


class ConfigResponse(BaseModel):
    config: dict[str, ConfigEntry]
    restart_required: list[str] = []
