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
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.pipeline import downloader, ffmpeg, summarizer, transcribe_providers, whisper_client
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
    if not jobs:
        session.commit()
    return len(jobs)


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


def _summarize_and_finalize(session, job: Job, transcript: Transcript, title: str, *, promoted: bool) -> None:
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
                _summarize_and_finalize(session, job, partial, partial.title, promoted=True)
                job_log.info("job done (resumed)", extra={"transcript_id": partial.id, "stage": "done"})
                return

            tmpdir = _make_job_tmpdir(settings.temp_dir, job_log)
            try:
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
                    _summarize_and_finalize(session, job, partial, partial.title, promoted=True)
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

                _summarize_and_finalize(session, job, transcript, dl.title, promoted=False)
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
