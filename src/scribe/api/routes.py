"""HTTP API — submit jobs, poll status, browse transcripts."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from scribe.api.schemas import JobCreate, JobView, TranscriptBrief, TranscriptFull
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.pipeline.downloader import DownloadError, extract_video_id

router = APIRouter()

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


@router.post("/jobs", response_model=JobView, status_code=201)
def create_job(body: JobCreate, session: Session = Depends(get_session)) -> JobView:
    """Submit a YouTube URL. Deduplicates by video_id against completed
    transcripts and in-flight jobs before queuing a new one."""
    try:
        video_id = extract_video_id(body.url)
    except DownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    done = session.scalar(
        select(Transcript).where(Transcript.video_id == video_id).order_by(Transcript.id.desc())
    )
    if done is not None:
        return JobView(job_id=done.job_id, url=body.url, video_id=video_id,
                       status=JobStatus.done.value, deduplicated=True, transcript=_brief(done))

    active = session.scalar(
        select(Job).where(Job.video_id == video_id, Job.status.in_(_ACTIVE)).order_by(Job.id.desc())
    )
    if active is not None:
        return JobView(job_id=active.id, url=active.url, video_id=video_id,
                       status=active.status.value, deduplicated=True)

    job = Job(url=body.url, video_id=video_id, status=JobStatus.queued, source=body.source)
    session.add(job)
    session.commit()
    return JobView(job_id=job.id, url=job.url, video_id=video_id, status=job.status.value)


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: int, session: Session = Depends(get_session)) -> JobView:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    transcript = session.scalar(select(Transcript).where(Transcript.job_id == job.id))
    return JobView(
        job_id=job.id, url=job.url, video_id=job.video_id, status=job.status.value,
        error=job.error, transcript=_brief(transcript) if transcript else None,
    )


@router.get("/transcripts", response_model=list[TranscriptBrief])
def list_transcripts(
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TranscriptBrief]:
    rows = session.scalars(
        select(Transcript).order_by(Transcript.id.desc()).limit(limit).offset(offset)
    ).all()
    return [_brief(t) for t in rows]


def _require_transcript(transcript_id: int, session: Session) -> Transcript:
    t = session.get(Transcript, transcript_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"transcript {transcript_id} not found")
    return t


@router.get("/transcripts/{transcript_id}", response_model=TranscriptFull)
def get_transcript(transcript_id: int, session: Session = Depends(get_session)) -> TranscriptFull:
    t = _require_transcript(transcript_id, session)
    return TranscriptFull(
        **_brief(t).model_dump(), job_id=t.job_id,
        transcript_md=t.transcript_md, summary_md=t.summary_md,
    )


@router.get("/transcripts/{transcript_id}/transcript.md")
def get_transcript_md(transcript_id: int, session: Session = Depends(get_session)) -> Response:
    t = _require_transcript(transcript_id, session)
    return Response(content=t.transcript_md, media_type="text/markdown; charset=utf-8")


@router.get("/transcripts/{transcript_id}/summary.md")
def get_summary_md(transcript_id: int, session: Session = Depends(get_session)) -> Response:
    t = _require_transcript(transcript_id, session)
    return Response(content=t.summary_md, media_type="text/markdown; charset=utf-8")
