"""Job queue worker — runs the scribe pipeline for queued jobs.

Per job:
  download (residential IP) -> ffmpeg 16k mono -> Vast whisper ->
  PERSIST transcript (summary_md=NULL) ->
  codex summary -> UPDATE transcript -> mark Job done.

The transcript row is committed **between whisper and summary** so a
summarizer failure (token revoked, prompt too long, …) does not discard the
expensive GPU work. The next /jobs submission for the same video_id sees a
partial transcript and re-runs only the summary step.

Each pipeline stage is timed into the `scribe_stage_duration_seconds`
histogram, status transitions land in `scribe_job_status_transitions_total`,
transcript inserts/promotions in `scribe_transcripts_total`, and Vast spend
accumulates into `scribe_vast_spend_usd_total`.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from scribe.api import cookie_jar
from scribe.api.routes import render_job_view, transition_job_status
from scribe.config import settings
from scribe.db.models import Job, JobStageEvent, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.pipeline import (
    downloader,
    ffmpeg,
    media_store,
    summarizer,
    telegram,
    transcribe_providers,
    uploads,
    whisper_client,
)
from scribe.pipeline.frontmatter_inject import inject_author_frontmatter
from scribe.worker import vast_budget

log = logging.getLogger("scribe.worker")

_POLL_INTERVAL = 5.0
# Loop tick in milliseconds — surfaced by the ops rollcall ("loop tick {LOOP_TICK_MS}ms").
LOOP_TICK_MS = int(_POLL_INTERVAL * 1000)
_WEBHOOK_TIMEOUT_S = 10.0
# Base retry schedule for webhook delivery. Each interval is randomized with
# ±10% partial jitter before sleeping, so concurrent deliveries to a
# slow-recovering callback do not synchronize into a thundering herd. The band
# is symmetric around the base, so the expected total retry budget is unchanged
# (mean == sum of bases); only the per-attempt spread is randomized.
_WEBHOOK_RETRY_BACKOFFS_S = (1.0, 4.0, 16.0)
_WEBHOOK_RETRY_JITTER = 0.10


def _jittered_backoff(base_s: float) -> float:
    """Return ``base_s`` with ±10% partial jitter applied.

    The result stays within ``[base_s * (1 - JITTER), base_s * (1 + JITTER)]``
    and is uniform over that band. Using a symmetric partial band (rather than
    AWS full jitter) keeps the total expected retry budget equivalent to the
    fixed schedule while still decorrelating concurrent retry streams."""
    spread = base_s * _WEBHOOK_RETRY_JITTER
    return random.uniform(base_s - spread, base_s + spread)
_INTERRUPTED_ON_STARTUP = (
    JobStatus.downloading,
    JobStatus.transcribing,
    JobStatus.summarizing,
)

# Live references to worker threads started via start_workers(); the ops
# rollcall reads this to flag the worker pool as `err` when any thread has died.
active_worker_threads: list[threading.Thread] = []


def _count_webhook_delivery(outcome: str) -> None:
    metrics.webhook_deliveries_total.labels(outcome=outcome).inc()


def _count_webhook_attempt(outcome: str) -> None:
    metrics.webhook_attempts_total.labels(outcome=outcome).inc()


def _deliver_webhook(session, job: Job) -> None:
    """Best-effort POST of the JobView JSON to job.callback_url. Never
    raises — failures land in webhook metrics and the log."""
    if not job.callback_url:
        _count_webhook_delivery("skipped")
        return
    # notify=False (#296) suppresses delivery even when a callback_url is set —
    # the caller opted out of the terminal-status push. getattr keeps the
    # contract honoured for Job-like objects that predate the column.
    if not getattr(job, "notify", True):
        _count_webhook_delivery("skipped")
        return
    body = render_job_view(session, job).model_dump(mode="json")
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if job.correlation_id:
        headers["X-Request-ID"] = job.correlation_id
    for attempt, backoff_s in enumerate((*_WEBHOOK_RETRY_BACKOFFS_S, None), start=1):
        try:
            req = urllib.request.Request(
                job.callback_url, data=data,
                headers=headers, method="POST",
            )
            start = time.monotonic()
            with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT_S) as resp:
                resp.read()
            metrics.webhook_delivery_latency_seconds.observe(time.monotonic() - start)
            _count_webhook_attempt("ok")
            _count_webhook_delivery("ok")
            log.info(
                "webhook delivered",
                extra={"job_id": job.id, "callback_url": job.callback_url, "attempt": attempt},
            )
            return
        except urllib.error.HTTPError as exc:
            _count_webhook_attempt("http_error")
            log.warning(
                "webhook delivery non-2xx: %s -> %s", job.callback_url, exc.code,
                extra={
                    "job_id": job.id,
                    "callback_url": job.callback_url,
                    "status": exc.code,
                    "attempt": attempt,
                },
            )
            if exc.code != 429 and not 500 <= exc.code <= 599:
                _count_webhook_delivery("http_error")
                return
            if backoff_s is None:
                _count_webhook_delivery("http_error")
                return
            time.sleep(_jittered_backoff(backoff_s))
            continue
        except ValueError as exc:
            _count_webhook_attempt("net_error")
            _count_webhook_delivery("net_error")
            log.warning(
                "webhook delivery invalid URL: %s -> %s", job.callback_url, exc,
                extra={
                    "job_id": job.id,
                    "callback_url": job.callback_url,
                    "error": str(exc),
                    "attempt": attempt,
                },
            )
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _count_webhook_attempt("net_error")
            log.warning(
                "webhook delivery network error: %s -> %s", job.callback_url, exc,
                extra={
                    "job_id": job.id,
                    "callback_url": job.callback_url,
                    "error": str(exc),
                    "attempt": attempt,
                },
            )
            if backoff_s is None:
                _count_webhook_delivery("net_error")
                return
            time.sleep(_jittered_backoff(backoff_s))


@contextmanager
def _time_stage(stage: str):
    """Emit a stage-duration sample on exit."""
    start = time.monotonic()
    try:
        yield
    finally:
        metrics.stage_duration_seconds.labels(stage=stage).observe(time.monotonic() - start)


def _set_job_status(session, job: Job, status: JobStatus) -> None:
    """Update Job.status, count the transition, commit."""
    if status == JobStatus.done and job.destroy_failed_at is not None:
        raise RuntimeError(f"job {job.id} cannot be marked done while Vast destroy is unconfirmed")
    transition_job_status(session, job, status)


def _record_vast_instance_created(job_id: int, instance_id: int, session_factory=SessionLocal) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.vast_instance_id = instance_id
        job.destroy_failed_at = None
        session.commit()


def _record_vast_destroy_failed(job_id: int, instance_id: int, session_factory=SessionLocal) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.vast_instance_id = instance_id
        job.destroy_failed_at = datetime.now(UTC)
        session.commit()


def _record_vast_destroy_succeeded(job_id: int, instance_id: int, session_factory=SessionLocal) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.vast_instance_id = instance_id
        job.destroy_failed_at = None
        session.commit()


def _enforce_monthly_cap(session_factory=SessionLocal) -> None:
    """Read-only DB check called from the whisper client right before it asks
    Vast for an instance. Raises WhisperError when the rolling 30-day spend
    (including a conservative in-flight reservation) exceeds the cap."""
    with session_factory() as session:
        vast_budget.enforce_monthly_cap(session)


def _retry_job_vast_destroy(job: Job) -> bool:
    if job.vast_instance_id is None:
        return True
    api_key = settings.vast_api_key.strip()
    if not api_key:
        job.destroy_failed_at = datetime.now(UTC)
        return False
    try:
        whisper_client._destroy_instance(api_key, job.vast_instance_id)
    except Exception:
        job.destroy_failed_at = datetime.now(UTC)
        return False
    job.destroy_failed_at = None
    return True


def retry_failed_vast_destroys(session) -> int:
    jobs = session.scalars(
        select(Job)
        .where(Job.vast_instance_id.is_not(None), Job.destroy_failed_at.is_not(None))
        .order_by(Job.id)
        .with_for_update(skip_locked=True)
    ).all()
    recovered = 0
    for job in jobs:
        if _retry_job_vast_destroy(job):
            recovered += 1
    session.commit()
    return recovered


def retry_pending_archives(session, *, limit: int = 3) -> int:
    """Re-attempt soft-failed archival uploads (#408).

    A transcript with ``media_error`` set but no ``media_object_key`` had its
    archive transcode/upload fail (e.g. an R2 outage) — the transcript + summary
    are already usable, so this sweep just retries the media step when the local
    source still exists. If the source is gone (cleaned up, or lost across a
    restart), the failure is marked permanent and no retry is attempted.

    Rows are claimed with ``FOR UPDATE SKIP LOCKED`` so concurrent workers do
    not race on the same transcript; readers (plain SELECTs) are unaffected by
    the row lock under MVCC, so holding it across the transcode is safe.
    """
    if not media_store.is_configured():
        return 0
    transcripts = session.scalars(
        select(Transcript)
        .where(
            Transcript.media_object_key.is_(None),
            Transcript.media_error.is_not(None),
            Transcript.media_error.not_like("%(permanent)"),
        )
        .order_by(Transcript.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    retried = 0
    for transcript in transcripts:
        source = uploads.find_source(transcript.job_id)
        if source is None:
            transcript.media_error = "archive source unavailable (permanent)"
            session.commit()
            continue
        job = session.get(Job, transcript.job_id)
        if job is None:
            session.commit()
            continue
        job_log = logging.LoggerAdapter(
            log,
            {"job_id": job.id, "video_id": transcript.video_id, "correlation_id": job.correlation_id},
        )
        try:
            key, size, content_type = _transcode_and_upload(job, transcript, source, job_log)
        except (ffmpeg.FfmpegError, media_store.MediaStoreError, OSError) as exc:
            transcript.media_error = f"{type(exc).__name__}: {exc}"
            session.commit()
            metrics.media_archive_total.labels(outcome="failed").inc()
            continue
        transcript.media_object_key = key
        transcript.media_size_bytes = size
        transcript.media_content_type = content_type
        transcript.media_uploaded_at = datetime.now(UTC)
        transcript.media_error = None
        session.commit()
        metrics.media_archive_total.labels(outcome="retry_ok").inc()
        uploads.cleanup(transcript.job_id)
        retried += 1
    return retried


def _claim_next_job(session) -> Job | None:
    """Atomically claim one queued job (FOR UPDATE SKIP LOCKED), set downloading."""
    job = session.scalar(
        select(Job)
        .where(Job.status == JobStatus.queued)
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.commit()
        return None
    _set_job_status(session, job, JobStatus.downloading)
    return job


def recover_interrupted_jobs(session) -> int:
    """Requeue jobs left mid-stage by a process restart.

    Workers run in-process with the FastAPI container. If the container is
    restarted during download/transcribe/summarize, no thread remains to finish
    that row. Requeueing on startup lets the normal pipeline resume; partial
    transcripts skip download+whisper and only re-run summary.
    """
    jobs = session.scalars(
        select(Job)
        .where(Job.status.in_(_INTERRUPTED_ON_STARTUP))
        .order_by(Job.id)
        .with_for_update()
    ).all()
    for job in jobs:
        old_status = job.status
        if job.vast_instance_id is not None and not _retry_job_vast_destroy(job):
            log.warning(
                "interrupted job Vast destroy still unconfirmed",
                extra={
                    "job_id": job.id,
                    "video_id": job.video_id,
                    "vast_instance_id": job.vast_instance_id,
                    "correlation_id": job.correlation_id,
                },
            )
            transition_job_status(session, job, JobStatus.failed)
            continue
        log.warning(
            "requeueing interrupted job",
            extra={
                "job_id": job.id,
                "video_id": job.video_id,
                "from_status": old_status.value,
                "correlation_id": job.correlation_id,
            },
        )
        transition_job_status(session, job, JobStatus.queued)

    # Archiving is a post-summary tail (#408): the transcript + summary are
    # already persisted, so a job interrupted mid-archiving must NOT be requeued
    # through the whole pipeline. Move it to `done` and let the periodic archive
    # sweep re-attempt the media step when the source survived the restart;
    # otherwise mark the archive failed-permanent rather than failing the job.
    archiving_jobs = session.scalars(
        select(Job).where(Job.status == JobStatus.archiving).order_by(Job.id).with_for_update()
    ).all()
    for job in archiving_jobs:
        transcript = session.scalar(select(Transcript).where(Transcript.job_id == job.id))
        if transcript is None:
            # No transcript yet (should not happen post-summary) — requeue.
            transition_job_status(session, job, JobStatus.queued)
            continue
        source = uploads.find_source(job.id)
        if source is not None:
            # Source survived the restart — retryable regardless of whether media
            # storage is configured right now. The archive sweep is a no-op while
            # storage is unconfigured and resumes once credentials return, so a
            # transient config loss must NOT be treated as source loss: deleting
            # the file + marking permanent here would strand the transcript with
            # no archival copy ever (#408, config loss != source loss).
            transcript.media_error = "archiving interrupted by restart; pending retry"
        else:
            # The uploaded source is genuinely gone — the archival copy can never
            # be produced, so mark it failed-permanent rather than failing the job.
            transcript.media_error = "archive source lost after restart (permanent)"
            uploads.cleanup(job.id)
        log.warning(
            "recovering interrupted archiving job",
            extra={
                "job_id": job.id,
                "video_id": job.video_id,
                "source_present": source is not None,
                "correlation_id": job.correlation_id,
            },
        )
        transition_job_status(session, job, JobStatus.done)

    if not jobs and not archiving_jobs:
        session.commit()
    return len(jobs) + len(archiving_jobs)


def _find_partial_transcript(
    session,
    video_id: str,
    owner_subject: str | None = None,
    owner_id: int | None = None,
) -> Transcript | None:
    """Return the most recent partial transcript (whisper done, summary missing)
    for this video_id, or None."""
    stmt = select(Transcript).where(Transcript.video_id == video_id, Transcript.summary_md.is_(None))
    if owner_id is not None:
        stmt = stmt.where(Transcript.owner_id == owner_id)
    if owner_subject:
        stmt = stmt.where(Transcript.owner_subject == owner_subject)
    return session.scalar(stmt.order_by(Transcript.id.desc()))


def _find_done_transcript(
    session,
    video_id: str,
    owner_subject: str | None = None,
    owner_id: int | None = None,
) -> Transcript | None:
    """Return the most recent completed transcript for this resolved video key."""
    stmt = select(Transcript).where(Transcript.video_id == video_id, Transcript.summary_md.is_not(None))
    if owner_id is not None:
        stmt = stmt.where(Transcript.owner_id == owner_id)
    if owner_subject:
        stmt = stmt.where(Transcript.owner_subject == owner_subject)
    return session.scalar(stmt.order_by(Transcript.id.desc()))


def _is_upload_job(job: Job) -> bool:
    """True for user-uploaded-file jobs (#408); False for URL/YouTube jobs.

    The dedup key for uploads is ``upload:<sha16>`` (see the /jobs/upload
    handler), so the video_id prefix is a stable, migration-free marker."""
    return bool(job.video_id) and job.video_id.startswith("upload:")


def _is_telegram_job(job: Job) -> bool:
    """True for Telegram media-reference jobs (#417); False for URL/YouTube/upload.

    A ``tg:<file_id>`` submission is keyed to ``telegram:<digest>`` by
    ``downloader.initial_video_key`` at submit time, so the video_id prefix is a
    stable, migration-free marker the worker uses to route the download stage
    through the secure Telegram adapter instead of yt-dlp."""
    return bool(job.video_id) and job.video_id.startswith("telegram:")


def _archive_object_key(transcript: Transcript, ext: str) -> str:
    """Stable, per-transcript R2 object key for the archival media copy."""
    safe_video = transcript.video_id.replace(":", "_").replace("/", "_")
    return f"media/{safe_video}/{transcript.id}.{ext}"


def _transcode_and_upload(job: Job, transcript: Transcript, source: Path, job_log) -> tuple[str, int, str]:
    """Transcode the downscaled archival copy and push it to R2 (#408).

    Video sources become a 480p H.264/AAC mp4; audio-only sources become Opus.
    The transcode temp lives in its own scratch dir (never the upload dir) so a
    leftover can never be mistaken for the source on a retry. Returns
    ``(object_key, size_bytes, content_type)``. Raises FfmpegError /
    MediaStoreError / OSError on failure — the caller isolates those into a soft
    archive failure."""
    probe = ffmpeg.probe_media(source)
    work = Path(tempfile.mkdtemp(prefix="scribe-archive-"))
    try:
        if probe.has_video:
            out = work / "archive.mp4"
            ffmpeg.transcode_archival_video(source, out)
            content_type, ext = "video/mp4", "mp4"
        else:
            out = work / "archive.opus"
            ffmpeg.transcode_archival_audio(source, out)
            content_type, ext = "audio/ogg", "opus"
        key = _archive_object_key(transcript, ext)
        size = out.stat().st_size
        media_store.upload_file(out, key, content_type)
        job_log.info(
            "archival media uploaded",
            extra={"stage": "archiving", "media_object_key": key, "media_size_bytes": size},
        )
        return key, size, content_type
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _archiving_stage_event(session, job: Job, *, finish: bool = False) -> None:
    """Record/close the archiving JobStageEvent (kept out of the SPA pipeline
    timeline so URL jobs stay byte-identical, but persisted for observability
    like every other stage)."""
    event = session.scalar(
        select(JobStageEvent).where(JobStageEvent.job_id == job.id, JobStageEvent.stage == "archiving")
    )
    if event is None:
        event = JobStageEvent(job_id=job.id, stage="archiving", started_at=datetime.now(UTC))
        session.add(event)
    if finish and event.finished_at is None:
        event.finished_at = datetime.now(UTC)


def _maybe_archive(session, job: Job, transcript: Transcript, source: Path | None, job_log) -> None:
    """Archive the uploaded source to R2 after the summary is persisted.

    No-op for URL/YouTube jobs (``source is None``) so their pipeline is
    byte-identical to today. A transcode/upload failure is ISOLATED: it records
    ``transcript.media_error`` and returns, so the job still finishes ``done``
    with a usable transcript + summary; the local source is kept for the retry
    sweep. On success the source directory is deleted."""
    if source is None:
        return
    if not media_store.is_configured():
        # Should not happen (the upload endpoint 503s when unconfigured), but if
        # storage config is lost after a job is accepted, degrade cleanly WITHOUT
        # deleting the source: keep it so the archive sweep can finish the media
        # step once storage is configured again. Deleting it here would strand the
        # transcript with no way to ever produce the archival copy (#408). The
        # error is left retryable (no "(permanent)" suffix) so the sweep picks it
        # up; the sweep itself is a no-op until media storage is configured.
        transcript.media_error = "media storage not configured at archive time; pending retry"
        session.commit()
        return

    transition_job_status(session, job, JobStatus.archiving)
    _archiving_stage_event(session, job)
    session.commit()

    try:
        with _time_stage("archiving"):
            key, size, content_type = _transcode_and_upload(job, transcript, source, job_log)
    except (ffmpeg.FfmpegError, media_store.MediaStoreError, OSError) as exc:
        session.rollback()
        transcript = session.get(Transcript, transcript.id)
        if transcript is not None:
            transcript.media_error = f"{type(exc).__name__}: {exc}"
        _archiving_stage_event(session, job, finish=True)
        session.commit()
        metrics.media_archive_total.labels(outcome="failed").inc()
        job_log.warning(
            "archive failed (soft); transcript + summary preserved",
            extra={"stage": "archiving", "error": f"{type(exc).__name__}: {exc}"},
        )
        return

    transcript = session.get(Transcript, transcript.id)
    if transcript is not None:
        transcript.media_object_key = key
        transcript.media_size_bytes = size
        transcript.media_content_type = content_type
        transcript.media_uploaded_at = datetime.now(UTC)
        transcript.media_error = None
    _archiving_stage_event(session, job, finish=True)
    session.commit()
    metrics.media_archive_total.labels(outcome="ok").inc()
    uploads.cleanup(job.id)


def _summarize_and_finalize(
    session,
    job: Job,
    transcript: Transcript,
    title: str,
    *,
    promoted: bool,
    archive_source: Path | None = None,
    job_log=None,
) -> None:
    """Run summarizer against an already-persisted transcript_md, update the
    row with summary + tags, mark the job done.

    The write-back is guarded by a compare-and-set. ``_find_partial_transcript``
    selects the partial with no row lock, so two jobs for the same video (e.g.
    a dedup TOCTOU enqueuing a second resume) can both pick up the same row.
    Before landing the summary we re-read the row under ``SELECT ... FOR UPDATE``
    and, if a concurrent worker already filled ``summary_md``, we adopt that
    finished summary instead of clobbering it with our (older) result — silent
    data loss otherwise (scr-549 / #353). The intermediate ``summarizing``
    commit releases any lock held at lookup time and the summary step is slow,
    so the lock has to be (re)taken here, right before the write."""
    if not job.summarize:
        # summarize=False (#296): skip the codex step entirely. The transcript
        # stays partial (summary_md=NULL) so the caller gets a transcript-only
        # result with no summary-provider spend. A later submit (summarize
        # default True) can resume the partial and fill in the summary.
        session.refresh(job)
        _maybe_archive(session, job, transcript, archive_source, job_log)
        session.refresh(job)
        _set_job_status(session, job, JobStatus.done)
        metrics.last_success_timestamp.set(time.time())
        _deliver_webhook(session, job)
        return
    _set_job_status(session, job, JobStatus.summarizing)
    with _time_stage("summary"):
        summary = summarizer.summarize(
            transcript.transcript_md, title=title, prompt_body=job.summary_prompt
        )
    summary_md = inject_author_frontmatter(
        summary.summary_md,
        author_name=transcript.author_name,
        author_handle=transcript.author_handle,
        author_url=transcript.author_url,
        source_platform=transcript.source_platform,
    )
    # Lock the row and re-read summary_md from the DB (our in-memory copy may be
    # stale: expire_on_commit is off, so the summarizing commit above did not
    # refresh it). If another resume already completed, keep its summary.
    session.refresh(transcript, with_for_update=True)
    if transcript.summary_md is not None:
        log.warning(
            "partial already summarized by a concurrent worker; keeping existing summary",
            extra={
                "job_id": job.id,
                "transcript_id": transcript.id,
                "video_id": transcript.video_id,
                "correlation_id": job.correlation_id,
            },
        )
    else:
        transcript.summary_md = summary_md
        transcript.short_description = summary.short_description
        transcript.tags = summary.tags or None
    session.refresh(job)
    _maybe_archive(session, job, transcript, archive_source, job_log)
    session.refresh(job)
    _set_job_status(session, job, JobStatus.done)
    metrics.transcripts_total.labels(kind="promoted" if promoted else "full").inc()
    metrics.last_success_timestamp.set(time.time())
    _deliver_webhook(session, job)


def _make_job_tmpdir(temp_dir: str, job_log: logging.LoggerAdapter) -> Path:
    """Create a per-job scratch dir under ``temp_dir``, degrading to the system
    temp dir when ``temp_dir`` is missing or not writable (issue #379).

    ``temp_dir`` can exist yet be non-writable (e.g. wrong ownership on
    ``/data/tmp``): ``mkdir(parents=True, exist_ok=True)`` then succeeds but
    ``mkdtemp(dir=temp_dir)`` raises, so both calls live inside one guard. We
    catch ``OSError`` (covers ``PermissionError``, ``FileNotFoundError``,
    ``NotADirectoryError``, …) and log a warning naming the original dir.
    """
    temp_root = Path(temp_dir)
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="scribe-job-", dir=temp_root))
    except OSError as exc:
        # Original temp_dir is embedded in the message: the structured LoggerAdapter
        # drops call-site ``extra`` keys, so the message text is what reaches the log.
        system_temp = tempfile.gettempdir()
        job_log.warning(
            "temp_dir %s unusable (%s); falling back to system temp %s",
            temp_root,
            exc,
            system_temp,
            extra={"stage": "download"},
        )
        return Path(tempfile.mkdtemp(prefix="scribe-job-", dir=system_temp))


def process_job(session, job: Job) -> None:
    """Run the full pipeline for a claimed job (already status=downloading)."""
    job_id = job.id
    job_log = logging.LoggerAdapter(
        log,
        {"job_id": job_id, "video_id": job.video_id, "correlation_id": job.correlation_id},
    )
    # Track busy-worker count for the ops dashboard. Bracketed with try/finally
    # so even unexpected exits (BaseException, abrupt thread shutdown) restore
    # the gauge — otherwise it would drift upward over the process lifetime.
    metrics.workers_busy.inc()
    # Upload jobs (#408) skip the yt-dlp download and, after summary, archive a
    # downscaled copy of their uploaded source to R2. `upload_source` is None
    # for URL/YouTube jobs, so every `archive_source=upload_source` below is a
    # no-op for them and their pipeline stays byte-identical to today.
    is_upload = _is_upload_job(job)
    # Telegram media references (#417) skip yt-dlp: the download stage resolves
    # the opaque `tg:<file_id>` through the secure adapter (Bot API getFile +
    # download) and presents a DownloadResult so the rest of the pipeline is
    # unchanged. Mutually exclusive with is_upload (distinct video_id prefixes).
    is_telegram = _is_telegram_job(job)
    upload_source = uploads.find_source(job_id) if is_upload else None
    try:
        try:
            # Resume path: a prior job already produced the transcript but its
            # summary step failed. Skip download+ffmpeg+whisper and just re-summarize.
            partial = _find_partial_transcript(session, job.video_id, job.owner_subject, job.owner_id)
            if partial is not None:
                job_log.info("resuming partial transcript", extra={"transcript_id": partial.id, "stage": "resume"})
                partial.job_id = job.id
                partial.owner_subject = job.owner_subject
                partial.owner_email = job.owner_email
                partial.owner_display_name = job.owner_display_name
                partial.owner_id = job.owner_id
                _summarize_and_finalize(
                    session, job, partial, partial.title,
                    promoted=True, archive_source=upload_source, job_log=job_log,
                )
                job_log.info("job done (resumed)", extra={"transcript_id": partial.id, "stage": "done"})
                return

            tmpdir = _make_job_tmpdir(settings.temp_dir, job_log)
            try:
                if is_upload:
                    # Upload jobs feed the user's file straight into the pipeline
                    # (no yt-dlp). Re-validate with ffprobe so a corrupt/missing
                    # source fails cleanly, then present a DownloadResult so the
                    # rest of the pipeline is unchanged.
                    if upload_source is None or not upload_source.exists():
                        raise RuntimeError("uploaded source file is missing")
                    with _time_stage("download"):
                        probe = ffmpeg.probe_media(upload_source)
                    dl = downloader.DownloadResult(
                        audio_path=upload_source,
                        title=job.title or upload_source.stem,
                        video_id=job.video_id,
                        duration_seconds=probe.duration_seconds,
                        source_platform="upload",
                    )
                elif is_telegram:
                    # Telegram jobs resolve+download through the secure adapter.
                    # A TelegramRefError (expired/inaccessible/oversize/not
                    # configured) carries a user-facing, secret-free message that
                    # lands in job.error via the outer handler below.
                    with _time_stage("download"):
                        dl = telegram.resolve_and_download(job.url, tmpdir)
                else:
                    # Per-job YouTube cookies (#313): the API handler stashed
                    # the validated blob keyed by job_id; take it once and let
                    # the downloader manage the 0600 temp lifecycle. ``None``
                    # falls through to the public-only download path.
                    job_cookies = cookie_jar.take(job_id)
                    with _time_stage("download"):
                        dl = downloader.download_audio(
                            job.url,
                            tmpdir,
                            cookies=job_cookies,
                            pot_base_url=settings.bgutil_pot_base_url or None,
                            max_bytes=settings.download_max_bytes,
                        )
                was_pending_key = job.video_id.startswith("pending:")
                job.title = dl.title
                if job.video_id != dl.video_id:
                    job.video_id = dl.video_id
                    job_log = logging.LoggerAdapter(
                        log,
                        {"job_id": job_id, "video_id": job.video_id, "correlation_id": job.correlation_id},
                    )
                session.commit()
                job_log.info("download done", extra={"title": dl.title, "stage": "download"})

                if was_pending_key:
                    done = _find_done_transcript(session, dl.video_id, job.owner_subject, job.owner_id)
                    if done is not None:
                        job_log.info(
                            "deduplicated after extraction",
                            extra={"transcript_id": done.id, "stage": "dedup"},
                        )
                        _set_job_status(session, job, JobStatus.done)
                        _deliver_webhook(session, job)
                        return

                partial = _find_partial_transcript(session, dl.video_id, job.owner_subject, job.owner_id)
                if partial is not None:
                    job_log.info("resuming partial transcript", extra={"transcript_id": partial.id, "stage": "resume"})
                    partial.job_id = job.id
                    partial.owner_subject = job.owner_subject
                    partial.owner_email = job.owner_email
                    partial.owner_display_name = job.owner_display_name
                    partial.owner_id = job.owner_id
                    # Backfill author/platform metadata from the fresh yt-dlp probe
                    # — partials persisted before scr-269 lack these columns.
                    if partial.author_name is None and dl.author_name:
                        partial.author_name = dl.author_name
                    if partial.author_handle is None and dl.author_handle:
                        partial.author_handle = dl.author_handle
                    if partial.author_url is None and dl.author_url:
                        partial.author_url = dl.author_url
                    if partial.source_platform is None and dl.source_platform:
                        partial.source_platform = dl.source_platform
                    _summarize_and_finalize(
                        session, job, partial, partial.title,
                        promoted=True, archive_source=upload_source, job_log=job_log,
                    )
                    job_log.info("job done (resumed)", extra={"transcript_id": partial.id, "stage": "done"})
                    return

                with _time_stage("ffmpeg"):
                    wav = ffmpeg.to_wav_16k_mono(dl.audio_path, tmpdir / "input-16k.wav")

                _set_job_status(session, job, JobStatus.transcribing)
                vast_session_factory = sessionmaker(
                    bind=session.get_bind(),
                    autoflush=False,
                    expire_on_commit=False,
                )
                with _time_stage("whisper"):
                    chain = transcribe_providers.build_provider_chain(
                        on_instance_created=lambda instance_id: _record_vast_instance_created(
                            job_id, instance_id, vast_session_factory
                        ),
                        on_destroy_failed=lambda instance_id: _record_vast_destroy_failed(
                            job_id, instance_id, vast_session_factory
                        ),
                        on_destroy_succeeded=lambda instance_id: _record_vast_destroy_succeeded(
                            job_id, instance_id, vast_session_factory
                        ),
                        check_monthly_cap=lambda: _enforce_monthly_cap(vast_session_factory),
                    )
                    tr = transcribe_providers.transcribe_with_chain(
                        chain,
                        transcribe_providers.TranscribeRequest(
                            wav=wav,
                            title=dl.title,
                            source_url=job.url,
                            duration_seconds=dl.duration_seconds,
                        ),
                    )
                # The ops rollcall reads this gauge to flag Vast.ai as `warn`
                # after 24h with no launches — only a real Vast launch counts.
                if tr.provider == "vast":
                    metrics.last_vast_launch_timestamp.set(time.time())
                    if tr.vast_cost:
                        metrics.vast_spend_usd_total.inc(tr.vast_cost)
                # Per-provider spend line (Vast + any hosted fallback). Lets the
                # spend caps / dashboards see each backend's cost separately.
                if tr.vast_cost:
                    metrics.transcribe_provider_spend_usd_total.labels(
                        provider=tr.provider
                    ).inc(tr.vast_cost)
                job_log.info("whisper done", extra={
                    "stage": "whisper",
                    "provider": tr.provider,
                    "lang": tr.detected_language,
                    "vast_cost": tr.vast_cost,
                    "duration_seconds": tr.duration_seconds,
                })

                # Persist partial transcript — locks in GPU work before summary
                duration = dl.duration_seconds or (
                    int(tr.duration_seconds) if tr.duration_seconds else None
                )
                transcript = Transcript(
                    job_id=job.id,
                    video_id=dl.video_id,
                    title=dl.title,
                    transcript_md=tr.transcript_md,
                    summary_md=None,
                    tags=None,
                    duration_seconds=int(duration) if duration else None,
                    lang=tr.detected_language,
                    # vast_cost feeds the Vast-only daily-spend cap, so only the
                    # Vast path populates it; a hosted fallback's cost lives in
                    # transcribe_provider_spend_usd_total instead.
                    vast_cost=tr.vast_cost if tr.provider == "vast" else None,
                    transcribe_provider=tr.provider,
                    author_name=dl.author_name,
                    author_handle=dl.author_handle,
                    author_url=dl.author_url,
                    source_platform=dl.source_platform,
                    owner_subject=job.owner_subject,
                    owner_email=job.owner_email,
                    owner_display_name=job.owner_display_name,
                    owner_id=job.owner_id,
                )
                session.add(transcript)
                session.commit()
                metrics.transcripts_total.labels(kind="partial").inc()

                _summarize_and_finalize(
                    session, job, transcript, dl.title,
                    promoted=False, archive_source=upload_source, job_log=job_log,
                )
                job_log.info("job done", extra={"transcript_id": transcript.id, "stage": "done", "title": dl.title})
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception as exc:
            session.rollback()
            failed = session.get(Job, job_id)
            if failed is not None:
                failed.error = f"{type(exc).__name__}: {exc}"
                _set_job_status(session, failed, JobStatus.failed)
                _deliver_webhook(session, failed)
            job_log.exception("job failed", extra={"stage": "failed", "error": f"{type(exc).__name__}: {exc}"})
            # A hard failure discards the uploaded source: the transcript (if any)
            # can still be re-summarized from stored text, but there is no
            # archival source to keep, and a re-upload supplies a fresh one.
            if is_upload:
                uploads.cleanup(job_id)
    finally:
        # Belt-and-braces: any return path above (resume short-circuit,
        # exception before the download stage, etc.) must not leave a
        # cookie blob in memory. The download path already pops it; this
        # discard covers the rest.
        cookie_jar.discard(job_id)
        metrics.workers_busy.dec()


def run_worker(stop: threading.Event) -> None:
    """Poll-claim-process loop; runs until `stop` is set."""
    while not stop.is_set():
        session = SessionLocal()
        try:
            retry_failed_vast_destroys(session)
            retry_pending_archives(session)
            job = _claim_next_job(session)
            if job is None:
                stop.wait(_POLL_INTERVAL)
                continue
            log.info("claimed job", extra={"job_id": job.id, "url": job.url, "stage": "claim", "correlation_id": job.correlation_id})
            process_job(session, job)
        except Exception:
            log.exception("worker loop error")
            stop.wait(_POLL_INTERVAL)
        finally:
            session.close()


def start_workers(n: int | None = None) -> tuple[list[threading.Thread], threading.Event]:
    """Spawn `n` daemon worker threads. Returns (threads, stop_event)."""
    n = max(1, n or settings.worker_concurrency)
    session = SessionLocal()
    try:
        recovered = recover_interrupted_jobs(session)
        if recovered:
            log.warning("requeued interrupted jobs on startup", extra={"count": recovered})
    except Exception:
        session.rollback()
        log.exception("interrupted job recovery failed")
    finally:
        session.close()

    stop = threading.Event()
    threads: list[threading.Thread] = []
    for i in range(n):
        thread = threading.Thread(
            target=run_worker, args=(stop,), name=f"scribe-worker-{i}", daemon=True
        )
        thread.start()
        threads.append(thread)
    # Replace (not append) so re-starts during reload land a clean roster — the
    # ops rollcall reads this to flag dead workers.
    active_worker_threads.clear()
    active_worker_threads.extend(threads)
    return threads, stop
