"""Worker extraction behavior for yt-dlp-supported providers."""
from __future__ import annotations

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.downloader import DownloadError, DownloadResult


def test_process_job_persists_resolved_non_youtube_video_id(db_session, monkeypatch, tmp_path):
    from scribe.pipeline import summarizer as summarizer_module
    from scribe.pipeline import whisper_client
    from scribe.worker import loop as worker_loop

    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    wav = tmp_path / "audio.wav"
    wav.write_text("wav", encoding="utf-8")

    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_a, **_k: DownloadResult(
            audio_path=audio,
            title="tweet video",
            video_id="twitter:2057105488165163198",
            duration_seconds=12,
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_a, **_k: wav)
    monkeypatch.setattr(
        worker_loop.whisper_client,
        "transcribe",
        lambda *_a, **_k: whisper_client.TranscribeResult(
            transcript_md="hello",
            detected_language="en",
            duration_seconds=12,
            backend="test",
            vast_instance_id=1,
            vast_cost=0.0,
        ),
    )
    monkeypatch.setattr(
        worker_loop.summarizer,
        "summarize",
        lambda *_a, **_k: summarizer_module.SummaryResult(summary_md="summary", tags=["x"]),
    )
    monkeypatch.setattr(worker_loop.shortlinks, "make_shortlink", lambda *_a, **_k: "short")
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_k: None)

    job = Job(
        url="https://x.com/i/status/2057105488165163198",
        video_id="pending:abc",
        status=JobStatus.downloading,
    )
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job.id).one()
    assert job.status == JobStatus.done
    assert job.video_id == "twitter:2057105488165163198"
    assert transcript.video_id == "twitter:2057105488165163198"


def test_process_job_records_extraction_failure_message(db_session, monkeypatch):
    from scribe.worker import loop as worker_loop

    def fail_download(*_args, **_kwargs):
        raise DownloadError("video extraction failed: unsupported URL")

    monkeypatch.setattr(worker_loop.downloader, "download_audio", fail_download)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_k: None)

    job = Job(url="not-a-supported-url", video_id="pending:bad", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert "video extraction failed" in job.error
    assert "YouTube video id" not in job.error


def test_process_job_dedups_after_non_youtube_extraction(db_session, monkeypatch, tmp_path):
    from scribe.worker import loop as worker_loop

    video_id = "twitter:2057105488165163200"
    done_job = Job(
        url="https://twitter.com/user/status/2057105488165163200",
        video_id=video_id,
        status=JobStatus.done,
    )
    db_session.add(done_job)
    db_session.flush()
    db_session.add(
        Transcript(
            job_id=done_job.id,
            video_id=video_id,
            title="existing",
            transcript_md="hello",
            summary_md="world",
        )
    )
    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    job = Job(
        url="https://x.com/i/status/2057105488165163200",
        video_id="pending:def",
        status=JobStatus.downloading,
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_a, **_k: DownloadResult(
            audio_path=audio,
            title="duplicate",
            video_id=video_id,
            duration_seconds=12,
        ),
    )
    def fail_ffmpeg(*_args, **_kwargs):
        raise AssertionError("ffmpeg should not run for a resolved duplicate")

    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", fail_ffmpeg)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_k: None)

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript_count = db_session.query(Transcript).filter_by(video_id=video_id).count()
    assert job.status == JobStatus.done
    assert job.video_id == video_id
    assert transcript_count == 1
