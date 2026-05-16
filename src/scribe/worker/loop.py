"""Job queue worker — runs the scribe pipeline for queued jobs.

Per job:
  download (residential IP) -> ffmpeg 16k mono -> Vast whisper ->
  PERSIST transcript (summary_md=NULL) ->
  codex summary -> UPDATE transcript -> shortlinks -> mark Job done.

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
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select

from scribe.api.routes import render_job_view, transition_job_status
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.pipeline import downloader, ffmpeg, shortlinks, summarizer, whisper_client

log = logging.getLogger("scribe.worker")

_POLL_INTERVAL = 5.0
# Loop tick in milliseconds — surfaced by the ops rollcall ("loop tick {LOOP_TICK_MS}ms").
LOOP_TICK_MS = int(_POLL_INTERVAL * 1000)
_WEBHOOK_TIMEOUT_S = 10.0
_WEBHOOK_RETRY_BACKOFFS_S = (1.0, 4.0, 16.0)
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
    body = render_job_view(session, job).model_dump(mode="json")
    data = json.dumps(body).encode("utf-8")
    for attempt, backoff_s in enumerate((*_WEBHOOK_RETRY_BACKOFFS_S, None), start=1):
        try:
            req = urllib.request.Request(
                job.callback_url, data=data,
                headers={"Content-Type": "application/json"}, method="POST",
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
            time.sleep(backoff_s)
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
            time.sleep(backoff_s)


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
    transition_job_status(session, job, status)


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
        log.warning(
            "requeueing interrupted job",
            extra={
                "job_id": job.id,
                "video_id": job.video_id,
                "from_status": old_status.value,
            },
        )
        transition_job_status(session, job, JobStatus.queued)
    if not jobs:
        session.commit()
    return len(jobs)


def _find_partial_transcript(session, video_id: str) -> Transcript | None:
    """Return the most recent partial transcript (whisper done, summary missing)
    for this video_id, or None."""
    return session.scalar(
        select(Transcript)
        .where(Transcript.video_id == video_id, Transcript.summary_md.is_(None))
        .order_by(Transcript.id.desc())
    )


def _mint_shortlinks(transcript: Transcript) -> None:
    """Idempotently mint scribe-web-UI shortlinks. Skips fields that are
    already set so re-runs (resume path) don't churn Chhoto."""
    base = settings.public_base_url.rstrip("/")
    if not transcript.summary_shortlink:
        transcript.summary_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}", verify=False
        )
    if not transcript.transcript_shortlink:
        transcript.transcript_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}/transcript.md", verify=False
        )


def _summarize_and_finalize(session, job: Job, transcript: Transcript, title: str, *, promoted: bool) -> None:
    """Run summarizer against an already-persisted transcript_md, update the
    row with summary + tags + shortlinks, mark the job done."""
    _set_job_status(session, job, JobStatus.summarizing)
    with _time_stage("summary"):
        summary = summarizer.summarize(transcript.transcript_md, title=title)
    transcript.summary_md = summary.summary_md
    transcript.short_description = summary.short_description
    transcript.tags = summary.tags or None
    session.flush()
    _mint_shortlinks(transcript)
    _set_job_status(session, job, JobStatus.done)
    metrics.transcripts_total.labels(kind="promoted" if promoted else "full").inc()
    metrics.last_success_timestamp.set(time.time())
    _deliver_webhook(session, job)


def process_job(session, job: Job) -> None:
    """Run the full pipeline for a claimed job (already status=downloading)."""
    job_id = job.id
    job_log = logging.LoggerAdapter(log, {"job_id": job_id, "video_id": job.video_id})
    # Track busy-worker count for the ops dashboard. Bracketed with try/finally
    # so even unexpected exits (BaseException, abrupt thread shutdown) restore
    # the gauge — otherwise it would drift upward over the process lifetime.
    metrics.workers_busy.inc()
    try:
        try:
            # Resume path: a prior job already produced the transcript but its
            # summary step failed. Skip download+ffmpeg+whisper and just re-summarize.
            partial = _find_partial_transcript(session, job.video_id)
            if partial is not None:
                job_log.info("resuming partial transcript", extra={"transcript_id": partial.id, "stage": "resume"})
                partial.job_id = job.id
                _summarize_and_finalize(session, job, partial, partial.title, promoted=True)
                job_log.info("job done (resumed)", extra={"transcript_id": partial.id, "stage": "done"})
                return

            Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
            tmpdir = Path(tempfile.mkdtemp(prefix="scribe-job-", dir=settings.temp_dir))
            try:
                with _time_stage("download"):
                    dl = downloader.download_audio(job.url, tmpdir)
                job.title = dl.title
                session.commit()
                job_log.info("download done", extra={"title": dl.title, "stage": "download"})

                with _time_stage("ffmpeg"):
                    wav = ffmpeg.to_wav_16k_mono(dl.audio_path, tmpdir / "input-16k.wav")

                _set_job_status(session, job, JobStatus.transcribing)
                with _time_stage("whisper"):
                    tr = whisper_client.transcribe(wav, title=dl.title, source_url=job.url)
                # The ops rollcall reads this gauge to flag Vast.ai as `warn`
                # after 24h with no launches.
                metrics.last_vast_launch_timestamp.set(time.time())
                if tr.vast_cost:
                    metrics.vast_spend_usd_total.inc(tr.vast_cost)
                job_log.info("whisper done", extra={
                    "stage": "whisper",
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
                    vast_cost=tr.vast_cost if tr.vast_cost is not None else None,
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
        metrics.workers_busy.dec()


def run_worker(stop: threading.Event) -> None:
    """Poll-claim-process loop; runs until `stop` is set."""
    while not stop.is_set():
        session = SessionLocal()
        try:
            job = _claim_next_job(session)
            if job is None:
                stop.wait(_POLL_INTERVAL)
                continue
            log.info("claimed job", extra={"job_id": job.id, "url": job.url, "stage": "claim"})
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
