"""Job queue worker — runs the scribe pipeline for queued jobs.

Per job: download (residential IP) -> ffmpeg 16k mono -> Vast whisper ->
codex summary -> shortlinks -> Transcript row. Status advances
queued -> downloading -> transcribing -> summarizing -> done|failed and is
committed at each step so the API can report live progress.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from pathlib import Path

from sqlalchemy import text

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


def process_job(session, job: Job) -> None:
    """Run the full pipeline for a claimed job (already status=downloading)."""
    job_id = job.id
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

        # 4. summarise via codex CLI
        job.status = JobStatus.summarizing
        session.commit()
        summary = summarizer.summarize(tr.transcript_md, title=dl.title)

        # 5. persist the Transcript row (need its id for shortlinks)
        duration = dl.duration_seconds or (
            int(tr.duration_seconds) if tr.duration_seconds else None
        )
        transcript = Transcript(
            job_id=job.id,
            video_id=dl.video_id,
            title=dl.title,
            transcript_md=tr.transcript_md,
            summary_md=summary.summary_md,
            tags=summary.tags or None,
            duration_seconds=int(duration) if duration else None,
            lang=tr.detected_language,
        )
        session.add(transcript)
        session.flush()  # assigns transcript.id

        # 6. shortlinks at scribe's own web-UI pages (verify=False: page may be
        #    unreachable until the service is deployed/up)
        base = settings.public_base_url.rstrip("/")
        transcript.summary_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}", verify=False
        )
        transcript.transcript_shortlink = shortlinks.make_shortlink(
            f"{base}/transcripts/{transcript.id}/transcript.md", verify=False
        )

        job.status = JobStatus.done
        session.commit()
        log.info("job %s done -> transcript %s (%s)", job_id, transcript.id, dl.title)
    except Exception as exc:  # any failure marks the job failed
        session.rollback()
        failed = session.get(Job, job_id)
        if failed is not None:
            failed.status = JobStatus.failed
            failed.error = f"{type(exc).__name__}: {exc}"
            session.commit()
        log.exception("job %s failed", job_id)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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
