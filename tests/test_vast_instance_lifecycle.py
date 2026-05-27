from __future__ import annotations

import pytest
from sqlalchemy import delete

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.whisper_client import TranscribeResult, WhisperError


def test_destroy_instance_confirms_missing_instance(monkeypatch):
    from scribe.pipeline import whisper_client

    calls: list[tuple[str, str]] = []

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "DELETE":
            return {}
        return {"instances": None}

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    whisper_client._destroy_instance("vast-test-key", 777)

    assert calls == [("DELETE", "/instances/777/"), ("GET", "/instances/777/")]


def test_destroy_instance_raises_when_delete_fails(monkeypatch):
    from scribe.pipeline import whisper_client

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        raise WhisperError("Vast API DELETE /instances/777/: HTTP 500: no")

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    with pytest.raises(WhisperError, match="HTTP 500"):
        whisper_client._destroy_instance("vast-test-key", 777)


def test_destroy_instance_raises_when_followup_still_shows_instance(monkeypatch):
    from scribe.pipeline import whisper_client

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        if method == "DELETE":
            return {}
        return {"instances": [{"id": 777, "actual_status": "running"}]}

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    with pytest.raises(WhisperError, match="still present"):
        whisper_client._destroy_instance("vast-test-key", 777)


def _patch_worker_pipeline(monkeypatch, tmp_path, transcribe):
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_args, **_kwargs: DownloadResult(
            audio_path=audio,
            title="Vast lifecycle video",
            video_id="vast-life-video",
            duration_seconds=42,
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_args, **_kwargs: wav)
    monkeypatch.setattr(worker_loop.summarizer, "summarize", lambda *_args, **_kwargs: _Summary())
    monkeypatch.setattr(worker_loop.whisper_client, "transcribe", transcribe)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_loop, "_deliver_webhook", lambda *_args, **_kwargs: None)


class _Summary:
    summary_md = "summary"
    short_description = "short"
    tags = ["tag"]


def test_process_job_records_vast_instance_and_reaches_done(db_session, monkeypatch, tmp_path):
    from scribe.worker import loop as worker_loop

    db_session.execute(delete(Job).where(Job.video_id.in_(["pending:vast-life", "vast-life-video"])))
    db_session.commit()

    def fake_transcribe(*_args, on_instance_created=None, on_destroy_succeeded=None, **_kwargs):
        on_instance_created(777)
        on_destroy_succeeded(777)
        return TranscribeResult("transcript", "en", 42, "fake", 777, 0.01)

    _patch_worker_pipeline(monkeypatch, tmp_path, fake_transcribe)
    job = Job(url="https://youtu.be/vast-life", video_id="pending:vast-life", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.done
    assert job.vast_instance_id == 777
    assert job.destroy_failed_at is None
    assert job.transcript is not None
    assert job.transcript.summary_md == "summary"


def test_process_job_marks_destroy_failure_and_does_not_reach_done(db_session, monkeypatch, tmp_path):
    from scribe.worker import loop as worker_loop

    db_session.execute(delete(Job).where(Job.video_id.in_(["pending:vast-fail", "vast-life-video"])))
    db_session.commit()

    def fake_transcribe(*_args, on_instance_created=None, on_destroy_failed=None, **_kwargs):
        on_instance_created(888)
        on_destroy_failed(888)
        raise WhisperError("destroy failed")

    _patch_worker_pipeline(monkeypatch, tmp_path, fake_transcribe)
    job = Job(url="https://youtu.be/vast-fail", video_id="pending:vast-fail", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.status != JobStatus.done
    assert job.vast_instance_id == 888
    assert job.destroy_failed_at is not None
    assert job.error is not None
    assert "WhisperError" in job.error


def test_recover_interrupted_job_retries_visible_vast_instance(db_session, monkeypatch):
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    video_id = "restart-visible-vast"
    db_session.execute(delete(Transcript).where(Transcript.video_id == video_id))
    db_session.execute(delete(Job).where(Job.video_id == video_id))
    db_session.execute(
        delete(Job).where(
            Job.status.in_(
                (JobStatus.downloading, JobStatus.transcribing, JobStatus.summarizing)
            )
        )
    )
    db_session.commit()

    destroyed: list[int] = []
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(
        worker_loop.whisper_client,
        "_destroy_instance",
        lambda _api_key, instance_id: destroyed.append(instance_id),
    )

    job = Job(
        url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.transcribing,
        vast_instance_id=999,
    )
    db_session.add(job)
    db_session.commit()

    assert worker_loop.recover_interrupted_jobs(db_session) == 1

    db_session.refresh(job)
    assert destroyed == [999]
    assert job.vast_instance_id == 999
    assert job.destroy_failed_at is None
    assert job.status == JobStatus.queued
