"""HTTP API — submit jobs, poll status, browse transcripts, ops endpoints.

GET /transcripts/{id} returns JSON for API consumers and redirects browser
HTML requests to the SPA detail route. The raw .md endpoints, admin
re-summarize + retry endpoints, and ops endpoints live here too.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.metadata
import logging
import re
import secrets
from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from scribe.api.schemas import (
    ActiveJobsResponse,
    ActiveJobView,
    BackupSnapshot,
    ConfigEntry,
    ConfigResponse,
    JobCreate,
    JobStageView,
    JobView,
    LibraryResponse,
    LibraryRow,
    OpsSnapshot,
    PromptActiveWrite,
    PromptDryRunCreate,
    PromptDryRunView,
    PromptListView,
    PromptVersionView,
    PromptWrite,
    SystemSnapshot,
    TranscriptBrief,
    TranscriptFull,
    WorkerPoolSnapshot,
)
from scribe.config import (
    RUNTIME_CONFIG,
    parse_runtime_config_value,
    serialize_runtime_config_value,
    settings,
)
from scribe.db.models import AppConfig, Job, JobStageEvent, JobStatus, Transcript
from scribe.db.query import escape_like
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.obs import ops as ops_helpers
from scribe.pipeline import prompts, shortlinks, summarizer
from scribe.pipeline.downloader import DownloadError, extract_video_id

router = APIRouter()
log = logging.getLogger("scribe.api")
_CONFIG_AUTH = HTTPBearer(auto_error=False)

# Postgres advisory-lock key used to serialise the daily-spend-cap check.
# Arbitrary 8-byte int derived from the literal so it's stable across deploys.
_CAP_LOCK_KEY = 0x5C8B_E5F3_A402_C0A8

_ACTIVE = (
    JobStatus.queued,
    JobStatus.downloading,
    JobStatus.transcribing,
    JobStatus.summarizing,
)
_PIPELINE_STAGES = (
    JobStatus.queued.value,
    JobStatus.downloading.value,
    JobStatus.transcribing.value,
    JobStatus.summarizing.value,
    JobStatus.done.value,
)
_STATUS_ORDER = {stage: idx for idx, stage in enumerate(_PIPELINE_STAGES)}


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _brief(t: Transcript) -> TranscriptBrief:
    return TranscriptBrief(
        id=t.id, video_id=t.video_id, title=t.title, tags=t.tags,
        duration_seconds=t.duration_seconds, lang=t.lang,
        summary_shortlink=t.summary_shortlink, transcript_shortlink=t.transcript_shortlink,
        created_at=t.created_at,
    )


def _full(t: Transcript) -> TranscriptFull:
    return TranscriptFull(
        id=t.id,
        video_id=t.video_id,
        title=t.title,
        tags=t.tags,
        duration_seconds=t.duration_seconds,
        lang=t.lang,
        summary_shortlink=t.summary_shortlink,
        transcript_shortlink=t.transcript_shortlink,
        created_at=t.created_at,
        job_id=t.job_id,
        transcript_md=t.transcript_md,
        summary_md=t.summary_md,
        vast_cost=t.vast_cost,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _summary_excerpt(summary_md: str | None, limit: int = 240) -> str:
    body = summary_md or ""
    body = re.sub(r"^---\s*\n.*?\n---\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    body = re.sub(r"[*_`]+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit]


def _library_row(t: Transcript) -> LibraryRow:
    return LibraryRow(
        id=t.id,
        video_id=t.video_id,
        title=t.title,
        tags=t.tags,
        lang=t.lang,
        duration_seconds=t.duration_seconds,
        vast_cost=t.vast_cost,
        created_at=t.created_at,
        summary_shortlink=t.summary_shortlink,
        transcript_shortlink=t.transcript_shortlink,
        summary_excerpt=_summary_excerpt(t.summary_md),
        is_partial=t.summary_md is None,
    )


def _latest_done_transcript(session: Session, video_id: str) -> Transcript | None:
    """A transcript counts as 'done' only when summary_md is non-NULL.
    Partial transcripts (whisper succeeded, summary failed) are intentionally
    excluded from dedup so the next /jobs submission triggers a re-summarize."""
    return session.scalar(
        select(Transcript)
        .where(Transcript.video_id == video_id, Transcript.summary_md.is_not(None))
        .order_by(Transcript.id.desc())
    )


def render_job_view(session: Session, job: Job) -> JobView:
    """Build the same JSON GET /jobs/<id> returns. Shared with the worker
    so webhook payloads stay in lockstep with what consumers see."""
    transcript = _latest_transcript_for_video(session, job.video_id)
    return JobView(
        job_id=job.id, url=job.url, video_id=job.video_id, status=job.status.value,
        error=job.error, callback_url=job.callback_url,
        transcript=_brief(transcript) if transcript else None,
    )


def _latest_transcript_for_video(session: Session, video_id: str) -> Transcript | None:
    return session.scalar(
        select(Transcript)
        .where(Transcript.video_id == video_id)
        .order_by(Transcript.id.desc())
    )


def _vast_spend_usd_since(session: Session, since: dt.datetime) -> float:
    """Sum of transcripts.vast_cost since the given timestamp. Skips NULL
    (warm-pool / mock runs that did not pay Vast)."""
    total = session.scalar(
        select(func.coalesce(func.sum(Transcript.vast_cost), 0.0))
        .where(Transcript.created_at >= since, Transcript.vast_cost.is_not(None))
    )
    return float(total or 0.0)


def _vast_spend_usd_between(session: Session, since: dt.datetime, until: dt.datetime) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(Transcript.vast_cost), 0.0))
        .where(
            Transcript.created_at >= since,
            Transcript.created_at < until,
            Transcript.vast_cost.is_not(None),
        )
    )
    return float(total or 0.0)


def _recent_vast_spend_usd(session: Session, hours: int = 24) -> float:
    """Convenience wrapper — rolling N-hour spend."""
    return _vast_spend_usd_since(session, dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours))


def record_job_stage_start(session: Session, job: Job, stage: JobStatus) -> None:
    """Create a stage event if it does not already exist for this job/stage."""
    if stage.value not in _STATUS_ORDER:
        return
    existing = session.scalar(
        select(JobStageEvent).where(
            JobStageEvent.job_id == job.id,
            JobStageEvent.stage == stage.value,
        )
    )
    if existing is None:
        session.add(JobStageEvent(job_id=job.id, stage=stage.value, started_at=dt.datetime.now(dt.UTC)))


def transition_job_status(session: Session, job: Job, status: JobStatus) -> None:
    """Update Job.status and persist stage timing edges."""
    now = dt.datetime.now(dt.UTC)
    old_status = job.status
    if old_status == status:
        job.updated_at = now
        session.commit()
        return

    if old_status.value in _STATUS_ORDER:
        current = session.scalar(
            select(JobStageEvent)
            .where(
                JobStageEvent.job_id == job.id,
                JobStageEvent.stage == old_status.value,
                JobStageEvent.finished_at.is_(None),
            )
            .order_by(JobStageEvent.started_at.desc())
        )
        if current is None:
            current = JobStageEvent(job_id=job.id, stage=old_status.value, started_at=job.created_at)
            session.add(current)
        current.finished_at = now

    job.status = status
    if status.value in _STATUS_ORDER:
        existing_new = session.scalar(
            select(JobStageEvent).where(
                JobStageEvent.job_id == job.id,
                JobStageEvent.stage == status.value,
            )
        )
        if existing_new is None:
            session.add(JobStageEvent(job_id=job.id, stage=status.value, started_at=now))
    metrics.job_status_transitions.labels(status=status.value).inc()
    session.commit()


def _config_response(*, restart_required: list[str] | None = None) -> ConfigResponse:
    return ConfigResponse(
        config={
            key: ConfigEntry(
                value=getattr(settings, key),
                source=settings.runtime_source(key),
                mutable=spec.mutable,
            )
            for key, spec in RUNTIME_CONFIG.items()
        },
        restart_required=restart_required or [],
    )


def require_config_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_CONFIG_AUTH),
) -> None:
    token = settings.config_api_bearer_token.strip()
    if not token:
        raise HTTPException(status_code=503, detail="config API bearer token is not configured")
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(status_code=401, detail="invalid bearer token")


@router.get("/api/config", response_model=ConfigResponse)
def get_config(_auth: None = Depends(require_config_auth)) -> ConfigResponse:
    return _config_response()


@router.post("/api/config", response_model=ConfigResponse)
def update_config(
    body: dict[str, object] = Body(...),
    session: Session = Depends(get_session),
    _auth: None = Depends(require_config_auth),
) -> ConfigResponse:
    unknown = sorted(set(body) - set(RUNTIME_CONFIG))
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown config keys: {', '.join(unknown)}")

    immutable = sorted(k for k in body if k in RUNTIME_CONFIG and not RUNTIME_CONFIG[k].mutable)
    if immutable:
        raise HTTPException(status_code=400, detail=f"read-only config keys: {', '.join(immutable)}")

    parsed: dict[str, bool | float | int | str] = {}
    errors: dict[str, str] = {}
    for key, value in body.items():
        try:
            parsed[key] = parse_runtime_config_value(key, value)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    existing_rows = dict(session.execute(select(AppConfig.key, AppConfig.value)).all())
    serialized_values = {
        key: serialize_runtime_config_value(value)
        for key, value in parsed.items()
    }
    try:
        settings.runtime_overlay(existing_rows | serialized_values)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for key, serialized in serialized_values.items():
        row = session.get(AppConfig, key)
        if row is None:
            session.add(AppConfig(key=key, value=serialized))
        else:
            row.value = serialized
    session.commit()
    restart_required = [
        key for key in parsed if RUNTIME_CONFIG[key].restart_required
    ]
    return _config_response(restart_required=restart_required)


@router.post("/api/config/rotate-token", status_code=501)
def rotate_token(_auth: None = Depends(require_config_auth)) -> None:
    # TODO(PRD §4.6): implement once the auth surface owns bearer-token rotation.
    raise HTTPException(status_code=501, detail="bearer-token rotation is not implemented yet")


@router.post("/jobs", response_model=JobView, status_code=201)
def create_job(body: JobCreate, session: Session = Depends(get_session)) -> JobView:
    """Submit a YouTube URL. Deduplicates by video_id against **done** transcripts
    and in-flight jobs. Partial transcripts (whisper succeeded but summary
    failed) do NOT dedup — the new job's worker will resume them."""
    try:
        video_id = extract_video_id(body.url)
    except DownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    done = _latest_done_transcript(session, video_id)
    if done is not None:
        # dedup-done bypasses the cost cap: no new GPU work happens
        return JobView(job_id=done.job_id, url=body.url, video_id=video_id,
                       status=JobStatus.done.value, deduplicated=True, transcript=_brief(done))

    active = session.scalar(
        select(Job).where(Job.video_id == video_id, Job.status.in_(_ACTIVE)).order_by(Job.id.desc())
    )
    if active is not None:
        # dedup-active also bypasses: the in-flight job is already spending its budget
        return JobView(job_id=active.id, url=active.url, video_id=video_id,
                       status=active.status.value, deduplicated=True)

    # Resume-path bypass: a partial transcript exists for this video_id
    # (whisper done, summary pending). The worker will skip download+whisper,
    # so the cap doesn't apply — and *blocking* this submission would make
    # the job permanently unrecoverable until enough spend rolls off.
    partial_exists = session.scalar(
        select(Transcript.id)
        .where(Transcript.video_id == video_id, Transcript.summary_md.is_(None))
        .limit(1)
    ) is not None

    # Only fresh, non-resume submissions trigger the rolling spend cap.
    cap = settings.daily_spend_cap_usd
    if cap > 0 and not partial_exists:
        # Serialise the check+insert so two concurrent POSTs can't both pass.
        # Transaction-scoped advisory lock — cheap; auto-released at commit.
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CAP_LOCK_KEY})
        spent = _recent_vast_spend_usd(session)
        if spent >= cap:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"daily Vast spend cap reached: ${spent:.4f} >= ${cap:.4f} (rolling 24h). "
                    "Resubmit after the window opens, or raise SCRIBE_DAILY_SPEND_CAP_USD."
                ),
            )

    job = Job(
        url=body.url, video_id=video_id, status=JobStatus.queued,
        source=body.source,
        callback_url=str(body.callback_url) if body.callback_url else None,
    )
    session.add(job)
    session.flush()
    record_job_stage_start(session, job, JobStatus.queued)
    session.commit()
    metrics.job_status_transitions.labels(status=JobStatus.queued.value).inc()
    return JobView(
        job_id=job.id, url=job.url, video_id=video_id, status=job.status.value,
        callback_url=job.callback_url,
    )


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: int, session: Session = Depends(get_session)) -> JobView:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return render_job_view(session, job)


@router.get("/transcripts", response_model=list[TranscriptBrief])
def list_transcripts(
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_partial: bool = Query(False, description="Also return partial transcripts (summary pending)."),
) -> list[TranscriptBrief]:
    stmt = select(Transcript).order_by(Transcript.id.desc())
    if not include_partial:
        stmt = stmt.where(Transcript.summary_md.is_not(None))
    rows = session.scalars(stmt.limit(limit).offset(offset)).all()
    return [_brief(t) for t in rows]


@router.get("/transcripts/{transcript_id}", response_model=TranscriptFull)
def get_transcript_detail(
    transcript_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    if _accepts_html(request):
        return RedirectResponse(url=f"/#/transcript/{transcript_id}", status_code=307)
    return _full(_require_transcript(transcript_id, session))


@router.get("/api/library", response_model=LibraryResponse)
def api_library(
    response: Response,
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="Fuzzy match against title and summary markdown."),
    tag: str | None = Query(None, description="Exact tag match."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> LibraryResponse:
    """List transcript rows for the SPA Library view without full summary bodies."""
    _no_store(response)
    stmt = select(Transcript)
    if q and q.strip():
        like = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(or_(Transcript.title.ilike(like), Transcript.summary_md.ilike(like)))
    if tag and tag.strip():
        stmt = stmt.where(Transcript.tags.any(tag.strip()))

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Transcript.id.desc()).limit(limit).offset(offset)).all()
    return LibraryResponse(
        rows=[_library_row(t) for t in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _stage_duration_s(event: JobStageEvent) -> int | None:
    if event.finished_at is None:
        return None
    return max(0, int((event.finished_at - event.started_at).total_seconds()))


def _stage_views(job: Job, events: dict[str, JobStageEvent]) -> dict[str, JobStageView]:
    status = job.status.value
    status_rank = _STATUS_ORDER.get(status, -1)
    views: dict[str, JobStageView] = {}
    for stage in _PIPELINE_STAGES:
        event = events.get(stage)
        state = "pending"
        if status == stage:
            state = "active" if stage != JobStatus.done.value else "done"
        elif status_rank > _STATUS_ORDER[stage]:
            state = "done"
        started_at = event.started_at if event else None
        finished_at = event.finished_at if event else None
        if stage == JobStatus.queued.value and started_at is None:
            started_at = job.created_at
        if state == "done" and finished_at is None and stage != JobStatus.done.value:
            finished_at = job.updated_at
        views[stage] = JobStageView(
            state=state,
            started_at=started_at,
            finished_at=finished_at,
            duration_s=_stage_duration_s(event) if event else None,
            progress=0.0 if state == "active" else None,
        )
    return views


@router.get("/api/jobs/active", response_model=ActiveJobsResponse, response_model_exclude_none=True)
def api_jobs_active(response: Response, session: Session = Depends(get_session)) -> ActiveJobsResponse:
    """Return all queued/in-flight jobs with derived pipeline stage state."""
    _no_store(response)
    jobs = session.scalars(select(Job).where(Job.status.in_(_ACTIVE)).order_by(Job.id)).all()
    if not jobs:
        return ActiveJobsResponse(jobs=[])

    job_ids = [job.id for job in jobs]
    events_by_job: dict[int, dict[str, JobStageEvent]] = {job.id: {} for job in jobs}
    for event in session.scalars(
        select(JobStageEvent)
        .where(JobStageEvent.job_id.in_(job_ids))
        .order_by(JobStageEvent.started_at)
    ):
        events_by_job[event.job_id][event.stage] = event

    video_ids = {job.video_id for job in jobs}
    transcripts = session.scalars(
        select(Transcript)
        .where(Transcript.video_id.in_(video_ids))
        .order_by(Transcript.id.desc())
    ).all()
    title_by_video: dict[str, str] = {}
    for transcript in transcripts:
        title_by_video.setdefault(transcript.video_id, transcript.title)

    now = dt.datetime.now(dt.UTC)
    return ActiveJobsResponse(
        jobs=[
            ActiveJobView(
                id=job.id,
                video_id=job.video_id,
                url=job.url,
                title=title_by_video.get(job.video_id),
                status=job.status.value,
                source=job.source,
                started_at=events_by_job[job.id].get(JobStatus.queued.value, None).started_at
                if JobStatus.queued.value in events_by_job[job.id]
                else job.created_at,
                elapsed_s=max(0, int((now - job.created_at).total_seconds())),
                stages=_stage_views(job, events_by_job[job.id]),
            )
            for job in jobs
        ]
    )


def _require_transcript(transcript_id: int, session: Session) -> Transcript:
    t = session.get(Transcript, transcript_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"transcript {transcript_id} not found")
    return t


def _prompt_error(exc: prompts.PromptError) -> HTTPException:
    if isinstance(exc, prompts.PromptNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/api/prompts", response_model=PromptListView, tags=["prompts"])
def list_prompt_versions() -> PromptListView:
    try:
        active, versions = prompts.list_prompts()
    except prompts.PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptListView(
        active_version=active,
        versions=[
            PromptVersionView(
                id=version.id,
                len_chars=version.len_chars,
                len_tokens_est=version.len_tokens_est,
                first_line=version.first_line,
                is_active=version.is_active,
            )
            for version in versions
        ],
    )


@router.post("/api/prompts/active", response_model=PromptListView, tags=["prompts"])
def set_active_prompt(body: PromptActiveWrite) -> PromptListView:
    try:
        prompts.set_active_version(body.version)
        active, versions = prompts.list_prompts()
    except prompts.PromptError as exc:
        raise _prompt_error(exc) from exc
    return PromptListView(
        active_version=active,
        versions=[
            PromptVersionView(
                id=version.id,
                len_chars=version.len_chars,
                len_tokens_est=version.len_tokens_est,
                first_line=version.first_line,
                is_active=version.is_active,
            )
            for version in versions
        ],
    )


@router.post("/api/prompts/dry-run", response_model=PromptDryRunView, tags=["prompts"])
async def dry_run_prompt(
    body: PromptDryRunCreate,
    session: Session = Depends(get_session),
) -> PromptDryRunView:
    try:
        prompts.validate_version(body.version)
        # Validate the template exists before waiting on the codex lock.
        prompts.read_prompt(body.version)
    except prompts.PromptError as exc:
        raise _prompt_error(exc) from exc

    t = _require_transcript(body.transcript_id, session)
    log.info(
        "prompt_dry_run",
        extra={"prompt_version": body.version, "transcript_id": t.id, "video_id": t.video_id},
    )
    try:
        result = await asyncio.to_thread(
            summarizer.summarize,
            t.transcript_md,
            title=t.title,
            lock_timeout=_RESUMMARIZE_LOCK_TIMEOUT_S,
            prompt_version=body.version,
        )
    except summarizer.LockTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"summarizer busy (codex job in flight): {exc}",
        ) from exc
    except summarizer.SummarizeError as exc:
        raise HTTPException(status_code=502, detail=f"summarizer failed: {exc}") from exc
    return PromptDryRunView(
        version=body.version,
        transcript_id=t.id,
        summary_md=result.summary_md,
        tags=result.tags,
    )


@router.get("/api/prompts/{version}", tags=["prompts"])
def get_prompt_version(version: str) -> Response:
    try:
        body = prompts.read_prompt(version)
    except prompts.PromptError as exc:
        raise _prompt_error(exc) from exc
    return Response(content=body, media_type="text/markdown; charset=utf-8")


@router.post("/api/prompts/{version}", status_code=204, tags=["prompts"])
def write_prompt_version(version: str, body: PromptWrite) -> Response:
    try:
        prompts.write_prompt(version, body.body)
    except prompts.PromptError as exc:
        raise _prompt_error(exc) from exc
    return Response(status_code=204)


@router.get("/transcripts/{transcript_id}/transcript.md")
def get_transcript_md(transcript_id: int, session: Session = Depends(get_session)) -> Response:
    t = _require_transcript(transcript_id, session)
    return Response(content=t.transcript_md, media_type="text/markdown; charset=utf-8")


@router.get("/transcripts/{transcript_id}/summary.md")
def get_summary_md(transcript_id: int, session: Session = Depends(get_session)) -> Response:
    t = _require_transcript(transcript_id, session)
    if t.summary_md is None:
        raise HTTPException(
            status_code=409,
            detail=f"transcript {transcript_id} is partial (no summary yet); "
                   "POST /transcripts/{id}/resummarize to retry.",
        )
    return Response(content=t.summary_md, media_type="text/markdown; charset=utf-8")


_RESUMMARIZE_LOCK_TIMEOUT_S = 120.0

# One-shot flash cookie consumed by the web detail view. Value layout:
#   "<level>|<percent-encoded message>"
# Percent-encoding avoids Starlette's SimpleCookie quoting whenever the message
# contains a space or other token-illegal char (RFC 6265 §4.1.1), which would
# otherwise leak raw quotes back to the client and the template.
FLASH_COOKIE = "scribe_flash"
CSRF_COOKIE = "scribe_csrf"
_FLASH_MAX_AGE = 30
_FLASH_LEVELS = frozenset({"success", "error", "info"})


def _accepts_html(request: Request) -> bool:
    """Browser form submissions send text/html in Accept; JSON API clients send
    application/json. Used to route POST /resummarize between the web flow
    (303 + flash cookie) and the JSON flow (TranscriptBrief)."""
    return "text/html" in request.headers.get("accept", "").lower()


def _flash_redirect(transcript_id: int, message: str, *, level: str = "success") -> RedirectResponse:
    if level not in _FLASH_LEVELS:
        level = "info"
    response = RedirectResponse(url=f"/transcripts/{transcript_id}", status_code=303)
    response.set_cookie(
        FLASH_COOKIE,
        f"{level}|{quote(message, safe='')}",
        max_age=_FLASH_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


def _validate_csrf(request: Request, csrf_token: str | None) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token or not csrf_token or not secrets.compare_digest(cookie_token, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


@router.post(
    "/transcripts/{transcript_id}/resummarize",
    response_model=TranscriptBrief,
    responses={303: {"description": "Web flow: redirect to transcript detail with flash cookie."}},
)
async def resummarize(
    transcript_id: int,
    request: Request,
    csrf_token: str | None = Form(None),
    session: Session = Depends(get_session),
):
    """Re-run the summarizer on an existing transcript (partial or done) and
    UPDATE the row. Useful when codex was down at the time of the original job.

    Content-negotiates: HTML clients (the web UI button) get a 303 redirect to
    the detail page with a one-shot flash cookie; JSON clients get the usual
    TranscriptBrief payload.

    Async to avoid pinning a FastAPI sync-handler thread for the full codex
    window (up to 600s) plus any lock-wait — the actual blocking work runs in
    `asyncio.to_thread`, and the codex lock acquisition is bounded by
    `_RESUMMARIZE_LOCK_TIMEOUT_S` (worker keeps the unbounded wait)."""
    if _accepts_html(request) or csrf_token is not None:
        _validate_csrf(request, csrf_token)
    t = _require_transcript(transcript_id, session)
    title = t.title
    transcript_md = t.transcript_md
    html_client = _accepts_html(request)
    try:
        result = await asyncio.to_thread(
            summarizer.summarize,
            transcript_md,
            title=title,
            lock_timeout=_RESUMMARIZE_LOCK_TIMEOUT_S,
        )
    except summarizer.LockTimeoutError as exc:
        if html_client:
            return _flash_redirect(transcript_id, f"Summarizer busy: {exc}", level="error")
        raise HTTPException(
            status_code=503,
            detail=f"summarizer busy (codex job in flight): {exc}",
        ) from exc
    except summarizer.SummarizeError as exc:
        if html_client:
            return _flash_redirect(transcript_id, f"Summarizer failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail=f"summarizer failed: {exc}") from exc

    # Re-read after the lock wait: the worker may have promoted this transcript
    # from partial to done while we were queued, in which case `was_partial`
    # would otherwise be stale-True and we'd double-count + overwrite.
    session.refresh(t)
    was_partial = t.summary_md is None

    t.summary_md = result.summary_md
    t.tags = result.tags or None
    base = settings.public_base_url.rstrip("/")
    if not t.summary_shortlink:
        t.summary_shortlink = shortlinks.make_shortlink(f"{base}/transcripts/{t.id}", verify=False)
    if not t.transcript_shortlink:
        t.transcript_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{t.id}/transcript.md", verify=False
        )

    if was_partial:
        # Promote the owning job from failed to done so dedup (POST /jobs) and
        # GET /jobs/<id> stay consistent — without this, dedup returns done
        # while the job_id still reports failed with the old error.
        owning_job = session.get(Job, t.job_id)
        if owning_job is not None and owning_job.status == JobStatus.failed:
            owning_job.error = None
            transition_job_status(session, owning_job, JobStatus.done)
        metrics.transcripts_total.labels(kind="promoted").inc()

    session.commit()
    if html_client:
        return _flash_redirect(t.id, "Summary regenerated.", level="success")
    return _brief(t)


_TERMINAL = (JobStatus.done, JobStatus.failed)


@router.post("/admin/jobs/{job_id}/retry", response_model=JobView, status_code=201)
def admin_retry_job(job_id: int, session: Session = Depends(get_session)) -> JobView:
    """Operator recovery (PRD §5.4): re-queue a terminal job as a new Job row.

    Bypasses POST /jobs dedup — a done job's transcript would otherwise short-
    circuit the re-run. Rejects non-terminal targets, and also rejects when
    any other job for the same video_id is already in flight, so we don't fork
    active work or double-spend on parallel retries. For `failed` jobs the
    original `error` is annotated with the new job id so GET /jobs/<old_id>
    points operators at the recovery; `done` jobs are left untouched because
    callers (and webhook consumers) treat `error != null` on a terminal row as
    a failure indicator — see `resummarize` which clears `error` on promotion.
    """
    # SELECT ... FOR UPDATE serialises concurrent retries of the same row, so
    # two simultaneous calls can't both pass the status check and queue twin
    # recovery jobs (the loser blocks until we commit, then re-reads).
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if job.status not in _TERMINAL:
        raise HTTPException(
            status_code=409,
            detail=(
                f"job {job_id} is {job.status.value} (non-terminal); "
                "retry is only allowed for done/failed jobs."
            ),
        )

    # Block forking when an earlier retry (or any /jobs submission) is already
    # in flight for the same video — operators retrying a terminal job twice
    # in a row would otherwise queue parallel pipelines and duplicate spend.
    active = session.scalar(
        select(Job)
        .where(Job.video_id == job.video_id, Job.status.in_(_ACTIVE), Job.id != job.id)
        .order_by(Job.id.desc())
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"video_id {job.video_id} already has active job {active.id} "
                f"({active.status.value}); cannot fork while in flight."
            ),
        )

    # retry-bypass: this is an explicit operator recovery, not a fresh user
    # submission, so the daily Vast spend cap (enforced in create_job) is
    # intentionally skipped — gating recovery on rolling spend would block
    # incident response exactly when it's most needed.
    new_job = Job(
        url=job.url, video_id=job.video_id, status=JobStatus.queued,
        source=job.source, callback_url=job.callback_url,
    )
    session.add(new_job)
    session.flush()
    record_job_stage_start(session, new_job, JobStatus.queued)

    # Only annotate `error` on already-failed jobs. Writing to `error` on a
    # `done` row would flip a clean terminal state into one that looks failed
    # to any client (or webhook consumer) reading `error != null` as failure.
    if job.status == JobStatus.failed:
        marker = f"recovered as job {new_job.id}"
        job.error = f"{job.error}; {marker}" if job.error else marker

    session.commit()
    metrics.job_status_transitions.labels(status=JobStatus.queued.value).inc()
    return JobView(
        job_id=new_job.id, url=new_job.url, video_id=new_job.video_id,
        status=new_job.status.value, callback_url=new_job.callback_url,
    )


# ----------------------------------------------------------------- ops endpoints
@router.get("/metrics", include_in_schema=False)
def get_metrics(session: Session = Depends(get_session)) -> Response:
    """Prometheus exposition. The queue-depth + rolling-spend gauges are
    sampled from the DB on every scrape — cheap, two small queries."""
    queue_depth = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(_ACTIVE))
    ) or 0
    metrics.worker_queue_depth.set(queue_depth)

    spend_24h = _recent_vast_spend_usd(session)
    cap = settings.daily_spend_cap_usd
    metrics.daily_spend_usd.set(spend_24h)
    metrics.daily_spend_cap_pct.set(metrics.compute_daily_spend_cap_pct(spend_24h, cap))

    body, ctype = metrics.export()
    return Response(content=body, media_type=ctype)


@router.get("/admin/backup-status")
def backup_status() -> dict:
    """Read the heartbeat written by the scribe-backups sidecar (PRD §4.12).

    Cheap, file-based, no DB hit — designed for `curl -f` healthcheck polling.
    Returns 200 with `stale=true` (and `last_success_ts=null`) when the file
    is missing or unreadable so the endpoint itself stays observable; callers
    decide on alerting via the `stale` flag. The future /api/ops endpoint
    reads the same data via `ops._backup_heartbeat()` — keep the two in sync
    by sharing the helper rather than duplicating the logic.
    """
    return ops_helpers._backup_heartbeat()


def _backup_snapshot() -> BackupSnapshot:
    payload = backup_status()
    return BackupSnapshot(
        last_success_iso=payload.get("last_success_iso"),
        age_seconds=payload.get("age_seconds"),
        stale_after=int(payload["stale_after_seconds"]),
        stale=bool(payload["stale"]),
        path=str(payload["path"]),
    )


def _spend_series_14d(session: Session, now: dt.datetime) -> list[float]:
    today = now.date()
    start_day = today - dt.timedelta(days=13)
    start = dt.datetime.combine(start_day, dt.time.min, tzinfo=dt.UTC)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
    rows = session.execute(
        text(
            """
            SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                   COALESCE(SUM(vast_cost), 0) AS spend
            FROM transcripts
            WHERE created_at >= :start
              AND created_at < :end
              AND vast_cost IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"start": start, "end": end},
    ).all()
    by_day = {row[0]: float(row[1] or 0.0) for row in rows}
    return [round(by_day.get(start_day + dt.timedelta(days=i), 0.0), 4) for i in range(14)]


def _service_version() -> str:
    try:
        return f"v{importlib.metadata.version('scribe')}"
    except importlib.metadata.PackageNotFoundError:
        return "vunknown"


@router.get("/api/ops", response_model=OpsSnapshot)
def api_ops(response: Response, session: Session = Depends(get_session)) -> OpsSnapshot:
    """One-shot JSON snapshot for the SPA Ops dashboard."""
    _no_store(response)
    now = dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=1)
    by_status = dict(
        session.execute(
            select(Job.status, func.count())
            .where(Job.created_at >= since)
            .group_by(Job.status)
        ).all()
    )
    transcripts_done = session.scalar(
        select(func.count()).select_from(Transcript)
        .where(Transcript.created_at >= since, Transcript.summary_md.is_not(None))
    ) or 0
    transcripts_partial = session.scalar(
        select(func.count()).select_from(Transcript)
        .where(Transcript.created_at >= since, Transcript.summary_md.is_(None))
    ) or 0
    queue_depth = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(_ACTIVE))
    ) or 0
    active_workers = session.scalar(
        select(func.count()).select_from(Job).where(
            Job.status.in_((JobStatus.downloading, JobStatus.transcribing, JobStatus.summarizing))
        )
    ) or 0
    # DB-derived proxy for worker occupancy. It can lag real OS thread state
    # after a worker crash until retry/recovery updates the in-flight jobs.

    return OpsSnapshot(
        window_days=1,
        jobs_by_status={str(k.value if hasattr(k, "value") else k): int(v) for k, v in by_status.items()},
        transcripts_done=int(transcripts_done),
        transcripts_partial=int(transcripts_partial),
        queue_depth=int(queue_depth),
        vast_spend_24h=round(_vast_spend_usd_since(session, now - dt.timedelta(hours=24)), 4),
        vast_spend_7d=round(_vast_spend_usd_since(session, now - dt.timedelta(days=7)), 4),
        vast_spend_30d=round(_vast_spend_usd_since(session, now - dt.timedelta(days=30)), 4),
        daily_spend_cap_usd=settings.daily_spend_cap_usd,
        spend_series_14d=_spend_series_14d(session, now),
        backup=_backup_snapshot(),
        worker_pool=WorkerPoolSnapshot(
            active=min(int(active_workers), settings.worker_concurrency),
            total=settings.worker_concurrency,
        ),
        system=[
            SystemSnapshot(label="scribe-service", value=_service_version(), status="ok"),
            SystemSnapshot(label="Postgres", value="connected", status="ok"),
        ],
    )


@router.get("/admin/daily-report")
def daily_report(session: Session = Depends(get_session), days: int = Query(1, ge=1, le=30)) -> dict:
    """Aggregate stats for the last N days (default 1). Intended for a small
    cron that POSTs the digest to Telegram — see README ops section."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    by_status = dict(
        session.execute(
            select(Job.status, func.count())
            .where(Job.created_at >= since)
            .group_by(Job.status)
        ).all()
    )
    transcripts_done = session.scalar(
        select(func.count()).select_from(Transcript)
        .where(Transcript.created_at >= since, Transcript.summary_md.is_not(None))
    ) or 0
    transcripts_partial = session.scalar(
        select(func.count()).select_from(Transcript)
        .where(Transcript.created_at >= since, Transcript.summary_md.is_(None))
    ) or 0
    queue_depth = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(_ACTIVE))
    ) or 0

    vast_spend_window = _vast_spend_usd_since(session, since)
    rolling_24h_since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    vast_spend_24h = _vast_spend_usd_since(session, rolling_24h_since)

    return {
        "window_days": days,
        "since_iso": since.isoformat(timespec="seconds"),
        "jobs_by_status": {str(k.value if hasattr(k, "value") else k): int(v) for k, v in by_status.items()},
        "transcripts_done": int(transcripts_done),
        "transcripts_partial": int(transcripts_partial),
        "current_queue_depth": int(queue_depth),
        "vast_spend_usd_window": round(vast_spend_window, 4),
        "vast_spend_usd_rolling_24h": round(vast_spend_24h, 4),
        "daily_spend_cap_usd": settings.daily_spend_cap_usd,
    }
