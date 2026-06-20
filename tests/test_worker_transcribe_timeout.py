from __future__ import annotations

import time

import pytest

from scribe.db.models import Job, JobStatus
from scribe.pipeline.downloader import DownloadResult


def _patch_stuck_transcribe(monkeypatch, tmp_path) -> list[int]:
    from scribe.config import settings
    from scribe.pipeline import whisper_client

    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")

    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 0.05)
    monkeypatch.setattr(whisper_client, "_ensure_local_ssh_key", lambda: (key_path, "ssh-ed25519 test"))
    monkeypatch.setattr(whisper_client, "_ensure_vast_ssh_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        whisper_client,
        "_select_offers",
        lambda *_args, **_kwargs: [{"id": 7, "dph_total": 0.1}],
    )
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_args, **_kwargs: 12345)
    monkeypatch.setattr(whisper_client, "_wait_for_ssh", lambda *_args, **_kwargs: ("127.0.0.1", 22))
    monkeypatch.setattr(whisper_client, "_wait_remote_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whisper_client, "_scp_to", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whisper_client, "_scp_from", lambda *_args, **_kwargs: None)
    destroyed: list[int] = []
    monkeypatch.setattr(whisper_client, "_destroy_instance", lambda _api_key, instance_id: destroyed.append(instance_id))

    def stuck_run(*_args, **_kwargs):
        time.sleep(1.0)
        raise AssertionError("stuck subprocess should outlive the wallclock timeout")

    monkeypatch.setattr(whisper_client, "_run", stuck_run)
    return destroyed


def test_transcribe_timeout_raises_and_destroys_instance(monkeypatch, tmp_path):
    from scribe.pipeline import whisper_client

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")
    destroyed = _patch_stuck_transcribe(monkeypatch, tmp_path)

    with pytest.raises(whisper_client.TranscribeTimeoutError, match="transcribe timed out"):
        whisper_client.transcribe(wav, title="timeout video", source_url="https://youtu.be/timeout-video")
    assert destroyed == [12345]


def test_transcribe_timeout_marks_job_failed(db_session, monkeypatch, tmp_path):
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
            title="timeout video",
            video_id="timeout-video",
            duration_seconds=42,
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_args, **_kwargs: wav)
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_args, **_kwargs: None)
    destroyed = _patch_stuck_transcribe(monkeypatch, tmp_path)

    job = Job(url="https://youtu.be/timeout-video", video_id="pending:timeout", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert destroyed == [12345]
    assert job.status == JobStatus.failed
    assert job.status != JobStatus.done
    assert job.error is not None
    assert "TranscribeTimeoutError" in job.error


# --- per-stage download timeout (#346) --------------------------------------
def test_download_timeout_marks_job_failed_with_reason(db_session, monkeypatch, tmp_path):
    """A download that exceeds the wall-clock timeout fails the job with a
    DownloadError(reason=download_timeout) surfaced in job.error and frees
    the worker (process_job returns instead of hanging)."""
    from scribe.config import settings
    from scribe.pipeline import downloader as dl_mod
    from scribe.worker import loop as worker_loop

    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_kw: None)

    def slow_download(*_args, **_kwargs):
        # Simulate yt-dlp hanging past the wall-clock budget. The downloader
        # itself enforces the timeout via download_audio(timeout_seconds=...),
        # so emulate the typed failure it would raise on a real hang.
        raise dl_mod.DownloadError(
            "download timed out after 0.1s (reason=download_timeout)",
            reason=dl_mod.REASON_DOWNLOAD_TIMEOUT,
        )

    monkeypatch.setattr(worker_loop.downloader, "download_audio", slow_download)

    job = Job(url="https://youtu.be/hang-video", video_id="pending:hang",
              status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    # Must return promptly (not hang) — proves the worker is freed.
    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert "DownloadError" in job.error
    assert "download_timeout" in job.error


def test_download_timeout_configurable_default(monkeypatch):
    """The default wall-clock budget is exposed on settings.download_timeout_s."""
    from scribe.config import Settings

    defaults = Settings()
    assert defaults.download_timeout_s == 600
