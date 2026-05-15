"""Job queue worker — runs the scribe pipeline for queued jobs.

Per job:
  download (residential IP) -> ffmpeg 16k mono -> Vast whisper ->
  PERSIST transcript (summary_md=NULL) ->
  codex summary -> UPDATE transcript -> shortlinks -> mark Job done.

The transcript row is committed **between whisper and summary** so a
summarizer failure (token revoked, prompt too long, …) does not discard the
expensive GPU work. The next /jobs submission for the same video_id sees a
partial transcript and re-runs only the summary step.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from pathlib import Path

from sqlalchemy import select, text

from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.pipeline import downloader, ffmpeg, shortlinks, summarizer, whisper_client

log = logging.getLogger("scribe.worker")

_POLL_INTERVAL = 5.0


def _claim_next_job(session) -> Job | None:
    """Atomically claim one queued job (FOR UPDATE SKIP LOCKED), set downloading."""
    row = session.execute(
        text(
            "UPDATE jobs SET status='downloading', updated_at=now() "
            "WHERE id = (SELECT id FROM jobs WHERE status='queued' "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id"
        )
    ).first()
    session.commit()
    return session.get(Job, row[0]) if row else None


def _find_partial_transcript(session, video_id: str) -> Transcript | None:
    """Return the most recent partial transcript (whisper done, summary missing)
    for this video_id, or None."""
    return session.scalar(
        select(Transcript)
        .where(Transcript.video_id == video_id, Transcript.summary_md.is_(None))
        .order_by(Transcript.id.desc())
    )


def _mint_shortlinks(transcript: Transcript) -> None:
    """Idempotently mint scribe-web-UI shortlinks on a transcript. Skips fields
    that are already set so re-runs (resume path) don't churn Chhoto."""
    base = settings.public_base_url.rstrip("/")
    if not transcript.summary_shortlink:
        transcript.summary_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}", verify=False
        )
    if not transcript.transcript_shortlink:
        transcript.transcript_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}/transcript.md", verify=False
        )


def _summarize_and_finalize(session, job: Job, transcript: Transcript, title: str) -> None:
    """Run summarizer against an already-persisted transcript_md, update the
    row with summary + tags + shortlinks, mark the job done.

    Raises on summarizer failure; caller's outer except handles the rollback."""
    job.status = JobStatus.summarizing
    session.commit()
    summary = summarizer.summarize(transcript.transcript_md, title=title)
    transcript.summary_md = summary.summary_md
    transcript.tags = summary.tags or None
    session.flush()  # ensure transcript.id stable for shortlinks (already had one)
    _mint_shortlinks(transcript)
    job.status = JobStatus.done
    session.commit()


def process_job(session, job: Job) -> None:
    """Run the full pipeline for a claimed job (already status=downloading)."""
    job_id = job.id
    try:
        # Resume path: a prior job already produced the transcript but its
        # summary step failed. Skip download+ffmpeg+whisper and just re-summarize.
        partial = _find_partial_transcript(session, job.video_id)
        if partial is not None:
            log.info(
                "job %s: resuming partial transcript %s (skip download+whisper)",
                job_id, partial.id,
            )
            # Hand the partial transcript over to the current job so
            # GET /jobs/<id> returns the same transcript.
            partial.job_id = job.id
            _summarize_and_finalize(session, job, partial, partial.title)
            log.info("job %s done (resumed) -> transcript %s", job_id, partial.id)
            return

        Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
        tmpdir = Path(tempfile.mkdtemp(prefix="scribe-job-", dir=settings.temp_dir))
        try:
            # 1. download the audio stream (residential IP)
            dl = downloader.download_audio(job.url, tmpdir)

            # 2. normalise to 16 kHz mono wav (single ffmpeg pass)
            wav = ffmpeg.to_wav_16k_mono(dl.audio_path, tmpdir / "input-16k.wav")

            # 3. transcribe on a Vast GPU instance
            job.status = JobStatus.transcribing
            session.commit()
            tr = whisper_client.transcribe(wav, title=dl.title, source_url=job.url)

            # 4. PERSIST partial transcript — locks in GPU work before summary
            duration = dl.duration_seconds or (
                int(tr.duration_seconds) if tr.duration_seconds else None
            )
            transcript = Transcript(
                job_id=job.id,
                video_id=dl.video_id,
                title=dl.title,
                transcript_md=tr.transcript_md,
                summary_md=None,  # partial; summarizer fills it next
                tags=None,
                duration_seconds=int(duration) if duration else None,
                lang=tr.detected_language,
            )
            session.add(transcript)
            session.commit()  # commit BEFORE summarizing so a codex failure preserves the transcript

            # 5. summarize + shortlinks + mark done
            _summarize_and_finalize(session, job, transcript, dl.title)
            log.info("job %s done -> transcript %s (%s)", job_id, transcript.id, dl.title)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception as exc:
        session.rollback()
        failed = session.get(Job, job_id)
        if failed is not None:
            failed.status = JobStatus.failed
            failed.error = f"{type(exc).__name__}: {exc}"
            session.commit()
        log.exception("job %s failed", job_id)


def run_worker(stop: threading.Event) -> None:
    """Poll-claim-process loop; runs until `stop` is set."""
    while not stop.is_set():
        session = SessionLocal()
        try:
            job = _claim_next_job(session)
            if job is None:
                stop.wait(_POLL_INTERVAL)
                continue
            log.info("claimed job %s: %s", job.id, job.url)
            process_job(session, job)
        except Exception:
            log.exception("worker loop error")
            stop.wait(_POLL_INTERVAL)
        finally:
            session.close()


def start_workers(n: int | None = None) -> tuple[list[threading.Thread], threading.Event]:
    """Spawn `n` daemon worker threads. Returns (threads, stop_event)."""
    n = max(1, n or settings.worker_concurrency)
    stop = threading.Event()
    threads: list[threading.Thread] = []
    for i in range(n):
        thread = threading.Thread(
            target=run_worker, args=(stop,), name=f"scribe-worker-{i}", daemon=True
        )
        thread.start()
        threads.append(thread)
    return threads, stop
