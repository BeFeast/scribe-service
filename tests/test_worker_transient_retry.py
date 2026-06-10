"""Worker job-level auto-retry on transient transport/capacity failures.

A transient scp/ssh drop (or capacity miss) must requeue the job up to
`job_max_transient_retries` times instead of dumping a manual-retry-only
`failed` row on the dashboard. Non-transient errors stay terminal."""
from __future__ import annotations

from scribe.db.models import Job, JobStatus
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.whisper_client import WhisperError


def _patch_pipeline(monkeypatch, tmp_path, transcribe_exc):
    from scribe.config import settings
    from scribe.pipeline import whisper_client
    from scribe.worker import loop as worker_loop

    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(settings, "job_max_transient_retries", 2)
    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_a, **_k: DownloadResult(
            audio_path=audio, title="vid", video_id="vid-xyz", duration_seconds=10
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_a, **_k: wav)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_loop, "_deliver_webhook", lambda *_a, **_k: None)

    def fake_transcribe(*_a, **_k):
        raise transcribe_exc

    monkeypatch.setattr(whisper_client, "transcribe", fake_transcribe)
    return worker_loop


def _run_once(worker_loop, db_session, job):
    job.status = JobStatus.downloading
    db_session.commit()
    worker_loop.process_job(db_session, job)
    db_session.refresh(job)


def test_transient_failure_requeues_until_cap_then_fails(db_session, monkeypatch, tmp_path):
    worker_loop = _patch_pipeline(
        monkeypatch,
        tmp_path,
        WhisperError("command failed (255): scp ...\nstderr:\nConnection closed by remote host"),
    )
    job = Job(url="https://youtu.be/vid-xyz", video_id="pending:vid", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    _run_once(worker_loop, db_session, job)
    assert job.status == JobStatus.queued
    assert job.attempts == 1

    _run_once(worker_loop, db_session, job)
    assert job.status == JobStatus.queued
    assert job.attempts == 2

    _run_once(worker_loop, db_session, job)
    assert job.status == JobStatus.failed
    assert job.attempts == 2
    assert "Connection closed by remote host" in (job.error or "")


def test_non_transient_failure_is_terminal_immediately(db_session, monkeypatch, tmp_path):
    worker_loop = _patch_pipeline(monkeypatch, tmp_path, ValueError("boom"))
    job = Job(url="https://youtu.be/vid-xyz", video_id="pending:vid2", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.attempts == 0
    assert "ValueError" in (job.error or "")


def test_budget_guard_is_terminal(db_session, monkeypatch, tmp_path):
    worker_loop = _patch_pipeline(
        monkeypatch, tmp_path, WhisperError("Vast budget guard tripped after 100s (~$0.30, cap $0.25)")
    )
    job = Job(url="https://youtu.be/vid-xyz", video_id="pending:vid3", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.attempts == 0
