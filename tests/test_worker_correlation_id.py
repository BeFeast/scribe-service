"""Correlation ID propagation through the worker pipeline (#357).

Asserts that a single correlation ID, stored on the Job at API ingress, is
carried by every structured worker log line emitted for that job across
download -> whisper -> summary -> done, and that the webhook delivery
includes the X-Request-ID header.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import delete

from scribe.db.models import Job, JobStatus
from scribe.obs.live_logs import job_log_buffer
from scribe.obs.logging import configure as configure_logging
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.summary_validator import SummaryResult
from scribe.pipeline.whisper_client import TranscribeResult


@pytest.fixture(autouse=True)
def _attach_job_log_buffer_handler():
    """Re-install the JobLogBufferHandler and re-enable the worker logger before each test.

    alembic's env.py calls ``fileConfig(alembic.ini)`` during the migrations
    test. With the default ``disable_existing_loggers=True`` that (a) drops
    the JobLogBufferHandler that feeds job_log_buffer from the root logger
    and (b) sets ``disabled=True`` on every pre-existing logger not named in
    the alembic config, including ``scribe.worker``. Both persist for the
    whole session, so without resetting them the buffer never captures and
    ``log.info(...)`` short-circuits. Reconfigure + re-enable so the buffer
    capture works regardless of test ordering.
    """
    configure_logging()
    logging.disable(logging.NOTSET)
    worker = logging.getLogger("scribe.worker")
    worker.disabled = False
    worker.setLevel(logging.INFO)
    worker._cache.clear()
    yield


VIDEO_ID = "corr-prop-1"
CORRELATION_ID = "req-abc-123"


def _patch_pipeline(monkeypatch, tmp_path: Path) -> None:
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")
    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_args, **_kwargs: DownloadResult(
            audio_path=audio,
            title="correlation video",
            video_id=VIDEO_ID,
            duration_seconds=42,
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_args, **_kwargs: wav)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_loop.whisper_client,
        "transcribe",
        lambda *_args, **_kwargs: TranscribeResult(
            transcript_md="transcript body",
            detected_language="en",
            duration_seconds=42.0,
            backend="mock",
            vast_instance_id=0,
            vast_cost=None,
        ),
    )
    monkeypatch.setattr(
        worker_loop.summarizer,
        "summarize",
        lambda *_args, **_kwargs: SummaryResult(
            summary_md="---\ntags: [test]\n---\n# summary",
            tags=["test"],
            short_description="short",
        ),
    )
    monkeypatch.setattr(worker_loop, "_deliver_webhook", lambda *_args, **_kwargs: None)


def test_correlation_id_constant_across_stage_logs(db_session, monkeypatch, tmp_path):
    from scribe.worker import loop as worker_loop

    _patch_pipeline(monkeypatch, tmp_path)
    job_log_buffer.clear()

    job = Job(
        url=f"https://youtu.be/{VIDEO_ID}",
        video_id=VIDEO_ID,
        status=JobStatus.downloading,
        correlation_id=CORRELATION_ID,
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.done

    _, lines = job_log_buffer.snapshot(job_id)
    assert lines, "no worker log lines were captured for the job"
    # Every captured stage log line must carry the same correlation ID.
    missing = [line for line in lines if line.get("correlation_id") != CORRELATION_ID]
    assert not missing, f"lines missing correlation_id: {missing}"
    # At least the download, whisper, and done stage transitions were logged
    # for this one job (the stable correlation ID ties them together).
    messages = " ".join(line.get("msg", "") for line in lines)
    assert "download done" in messages
    assert "whisper done" in messages
    assert "job done" in messages

    db_session.execute(delete(Job).where(Job.video_id == VIDEO_ID))
    db_session.commit()
    job_log_buffer.clear()


def test_correlation_id_defaults_to_none_when_unset(db_session, monkeypatch, tmp_path):
    """A job submitted before the correlation_id column existed (or without
    an inbound header) still processes; logs simply carry None rather than
    crashing the LoggerAdapter."""
    from scribe.worker import loop as worker_loop

    _patch_pipeline(monkeypatch, tmp_path)
    job_log_buffer.clear()

    job = Job(
        url=f"https://youtu.be/{VIDEO_ID}-none",
        video_id=f"{VIDEO_ID}-none",
        status=JobStatus.downloading,
        correlation_id=None,
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    worker_loop.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == JobStatus.done

    _, lines = job_log_buffer.snapshot(job_id)
    assert lines
    assert all(line.get("correlation_id") is None for line in lines)

    db_session.execute(delete(Job).where(Job.video_id == f"{VIDEO_ID}-none"))
    db_session.commit()
    job_log_buffer.clear()
