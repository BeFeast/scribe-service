"""Pydantic request/response shapes for the HTTP API."""
from __future__ import annotations

import datetime as dt

from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, Field

# Per-job YouTube cookies blob ceiling. A real Netscape cookies.txt for
# youtube.com sits around 8–20 KB; 256 KB is a generous cap that still
# blocks accidental log-file uploads. Kept here (not in settings) because
# this is an API-contract constant, not an operator knob.
YOUTUBE_COOKIES_MAX_BYTES = 256 * 1024

# Per-job summary-prompt override ceiling (#296). Mirrors the on-disk prompt
# template limit (scribe.pipeline.prompts.MAX_PROMPT_CHARS = 16 KiB) so a
# custom Capture-sheet prompt can be at most as large as a stored template.
# pydantic rejects anything longer at the API boundary (422).
SUMMARY_PROMPT_MAX_CHARS = 16 * 1024


class CookieValidationError(ValueError):
    """Raised when youtube_cookies fails size/format checks. The message
    NEVER contains any portion of the cookie value — callers surface the
    message in 422 responses and log lines."""


def validate_youtube_cookies(blob: str) -> None:
    """Size-check + Netscape cookies.txt format check.

    Format reference (Mozilla/curl): one cookie per line, tab-separated,
    7 fields: domain, include_subdomains, path, secure, expiry, name, value.
    Lines starting with `#` (including the `#HttpOnly_` prefix) and blank
    lines are ignored. We require at least one well-formed data line so an
    empty/comment-only blob is rejected as useless. Raises
    CookieValidationError with a value-free message on any failure."""
    if len(blob.encode("utf-8")) > YOUTUBE_COOKIES_MAX_BYTES:
        raise CookieValidationError(
            f"youtube_cookies exceeds {YOUTUBE_COOKIES_MAX_BYTES} byte limit"
        )
    data_lines = 0
    for raw in blob.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise CookieValidationError(
                "malformed Netscape cookies.txt: each data line must have "
                "7 tab-separated fields"
            )
        data_lines += 1
    if data_lines == 0:
        raise CookieValidationError("cookies blob contains no cookie entries")


class JobCreate(BaseModel):
    url: str
    source: str | None = None
    # If set, scribe POSTs the JobView JSON here on terminal status. Best-
    # effort delivery — failures are logged + counted but don't fail the job.
    # AnyHttpUrl rejects malformed values at the API boundary (422) so the
    # worker never has to deal with `http:/typo` or similar at delivery time.
    callback_url: AnyHttpUrl | None = None
    # Optional Netscape cookies.txt blob, supplied per-job by an owner /
    # extension-token caller (see #308 anti-bot layer B). Size + format are
    # validated inside the route so the 422 response never echoes the value
    # (pydantic's RequestValidationError includes the offending input).
    # The value is never persisted to the DB and never logged.
    youtube_cookies: str | None = None
    # Per-job pipeline toggles for the mobile Capture sheet (#296). All optional
    # and default to today's behavior, so existing callers ({url, source, ...})
    # are unaffected:
    #   summarize=False      → skip the codex summary step; the job finishes
    #                          with a transcript-only (partial) result and no
    #                          summary-provider spend.
    #   notify=False         → suppress the terminal-status webhook delivery
    #                          even when callback_url is set.
    #   summary_prompt=<str> → override the active prompt template for this job
    #                          only (blank/whitespace is treated as omitted).
    summarize: bool = True
    notify: bool = True
    summary_prompt: str | None = Field(default=None, max_length=SUMMARY_PROMPT_MAX_CHARS)


class PreflightResponse(BaseModel):
    """Offline yt-dlp URL-support verdict (#339).

    ``single_media`` is the only auto-submit signal: it is true only when a
    dedicated extractor matches AND its ``_RETURN_TYPE`` is ``"video"``.
    Containers (playlist/feed/channel/search) report ``supported=True`` with
    ``single_media=False`` and must be confirmed by the user, never
    auto-submitted. See :func:`scribe.api.preflight.match_url`."""

    supported: bool
    extractor: str | None
    return_type: str | None
    single_media: bool
    generic_only: bool


class CurrentUserView(BaseModel):
    authenticated: bool
    kind: str
    role: str
    user_id: int | None = None
    owner_id: int | None = None
    email: str | None = None
    display_name: str | None = None


class UserAdminCreate(BaseModel):
    email: str
    display_name: str | None = None
    role: str = "user"


class UserAdminRoleUpdate(BaseModel):
    role: str


class UserAdminView(BaseModel):
    id: int
    owner_id: int
    clerk_subject: str | None = None
    primary_email: str
    display_name: str | None = None
    role: str
    disabled: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class ExtensionTokenCreate(BaseModel):
    label: str | None = None


class ExtensionTokenView(BaseModel):
    token: str
    token_type: str = "bearer"


class MachineBearerRotateView(BaseModel):
    # The freshly rotated token is returned exactly once; only its hash is
    # persisted. The previous generation stays accepted for grace_seconds.
    token: str
    token_type: str = "bearer"
    grace_seconds: int


class TranscriptBrief(BaseModel):
    id: int
    video_id: str
    title: str
    tags: list[str] | None = None
    duration_seconds: int | None = None
    lang: str | None = None
    summary_shortlink: str | None = None
    transcript_shortlink: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    author_name: str | None = None
    author_handle: str | None = None
    author_url: str | None = None
    source_platform: str | None = None
    created_at: dt.datetime


class TranscriptFull(TranscriptBrief):
    job_id: int
    # Short, download-only preview of the transcript body (#384). The full text
    # is fetched on demand via GET /transcripts/:id/transcript.md so the detail
    # payload stays light regardless of transcript length.
    transcript_excerpt: str
    summary_md: str | None
    vast_cost: float | None = None


class JobStageView(BaseModel):
    state: str
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    duration_s: int | None = None
    progress: float | None = None
    note: str | None = None


class ShareLinkCreate(BaseModel):
    target_kind: str = "page"
    expires_at: AwareDatetime | None = None
    label: str | None = None
    recipient_note: str | None = None


class ShareLinkView(BaseModel):
    id: int
    transcript_id: int
    target_kind: str
    created_by: str
    created_at: dt.datetime
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    label: str | None = None
    recipient_note: str | None = None
    token_hint: str
    share_url: str | None = None


class ShareLinkCreated(ShareLinkView):
    token: str
    share_url: str


class JobView(BaseModel):
    job_id: int
    url: str
    video_id: str
    title: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    status: str
    error: str | None = None
    deduplicated: bool = False
    callback_url: str | None = None
    correlation_id: str | None = None
    transcript: TranscriptBrief | None = None
    started_at: dt.datetime | None = None
    elapsed_s: int | None = None
    stages: dict[str, JobStageView] | None = None


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
    prompt_body: str | None = None


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
    source_url: str | None = None
    source_label: str | None = None
    summary_excerpt: str
    is_partial: bool


class LibraryResponse(BaseModel):
    rows: list[LibraryRow]
    total: int
    limit: int
    offset: int


class ActiveJobView(BaseModel):
    id: int
    video_id: str
    url: str
    source_url: str | None = None
    source_label: str | None = None
    title: str | None = None
    status: str
    source: str | None = None
    started_at: dt.datetime
    elapsed_s: int
    stages: dict[str, JobStageView]


class ActiveJobsResponse(BaseModel):
    jobs: list[ActiveJobView]


class FailedJobView(BaseModel):
    id: int
    video_id: str
    url: str
    title: str | None = None
    source: str | None = None
    error: str | None = None
    failed_at: dt.datetime
    stages: dict[str, JobStageView]


class RecentFailuresResponse(BaseModel):
    jobs: list[FailedJobView]


class JobHistoryRow(BaseModel):
    id: int
    video_id: str
    url: str
    source_url: str | None = None
    source_label: str | None = None
    title: str | None = None
    status: str
    source: str | None = None
    error: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    transcript_id: int | None = None


class JobHistoryResponse(BaseModel):
    jobs: list[JobHistoryRow]
    total: int
    limit: int
    offset: int


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


class AuthConfigResponse(BaseModel):
    clerk_publishable_key: str
    clerk_frontend_api: str
    trusted_network: bool
