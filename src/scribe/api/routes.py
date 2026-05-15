"""HTTP API — submit jobs, poll status, browse transcripts, ops endpoints.

Note: GET /transcripts/{id} is served as HTML by web/views.py (the worker
mints the summary shortlink against that path). The JSON API here keeps the
job endpoints, the transcript list, the raw .md endpoints, the admin
re-summarize endpoint, and the ops endpoints (/metrics, /admin/daily-report).
"""
from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scribe.api.schemas import JobCreate, JobView, TranscriptBrief
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.pipeline import shortlinks, summarizer
from scribe.pipeline.downloader import DownloadError, extract_video_id

router = APIRouter()

# Postgres advisory-lock key used to serialise the daily-spend-cap check.
# Arbitrary 8-byte int derived from the literal so it's stable across deploys.
_CAP_LOCK_KEY = 0x5C8B_E5F3_A402_C0A8

_ACTIVE = (
    JobStatus.queued,
    JobStatus.downloading,
    JobStatus.transcribing,
    JobStatus.summarizing,
)


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


def _recent_vast_spend_usd(session: Session, hours: int = 24) -> float:
    """Convenience wrapper — rolling N-hour spend."""
    return _vast_spend_usd_since(session, dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours))


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


def _require_transcript(transcript_id: int, session: Session) -> Transcript:
    t = session.get(Transcript, transcript_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"transcript {transcript_id} not found")
    return t


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


@router.post("/transcripts/{transcript_id}/resummarize", response_model=TranscriptBrief)
async def resummarize(
    transcript_id: int, session: Session = Depends(get_session)
) -> TranscriptBrief:
    """Re-run the summarizer on an existing transcript (partial or done) and
    UPDATE the row. Useful when codex was down at the time of the original job.

    Async to avoid pinning a FastAPI sync-handler thread for the full codex
    window (up to 600s) plus any lock-wait — the actual blocking work runs in
    `asyncio.to_thread`, and the codex lock acquisition is bounded by
    `_RESUMMARIZE_LOCK_TIMEOUT_S` (worker keeps the unbounded wait)."""
    t = _require_transcript(transcript_id, session)
    title = t.title
    transcript_md = t.transcript_md
    try:
        result = await asyncio.to_thread(
            summarizer.summarize,
            transcript_md,
            title=title,
            lock_timeout=_RESUMMARIZE_LOCK_TIMEOUT_S,
        )
    except summarizer.LockTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"summarizer busy (codex job in flight): {exc}",
        ) from exc
    except summarizer.SummarizeError as exc:
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
            owning_job.status = JobStatus.done
            owning_job.error = None
            metrics.job_status_transitions.labels(status=JobStatus.done.value).inc()
        metrics.transcripts_total.labels(kind="promoted").inc()

    session.commit()
    return _brief(t)


# ----------------------------------------------------------------- ops endpoints
@router.get("/metrics", include_in_schema=False)
def get_metrics(session: Session = Depends(get_session)) -> Response:
    """Prometheus exposition. The queue-depth gauge is sampled from the DB
    on every scrape — cheap, single small query."""
    queue_depth = session.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(_ACTIVE))
    ) or 0
    metrics.worker_queue_depth.set(queue_depth)
    body, ctype = metrics.export()
    return Response(content=body, media_type=ctype)


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
