from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.whisper_client import TranscribeResult, WhisperError


def test_destroy_instance_confirms_missing_instance(monkeypatch):
    from scribe.pipeline import whisper_client

    calls: list[tuple[str, str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(whisper_client, "_sleep", sleeps.append)

    def fake_vast(_api_key, method, path, payload=None, timeout=45, **_retry_kwargs):
        calls.append((method, path))
        if method == "DELETE":
            return {}
        return {"instances": None}

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    whisper_client._destroy_instance("vast-test-key", 777)

    assert calls == [("DELETE", "/instances/777/"), ("GET", "/instances/777/")]
    # DELETE and the confirm GET share a rate-limit bucket family; the confirm
    # gets its own interval instead of a zero-gap burst (#426).
    assert sleeps == [whisper_client._DESTROY_CONFIRM_DELAY_SECONDS]


@pytest.mark.parametrize("gone_code", [404, 410])
def test_destroy_instance_treats_delete_gone_as_already_destroyed(monkeypatch, gone_code):
    """#426 D5: a retried destroy can find the instance already gone (the
    first DELETE landed but its confirm failed). That must read as success —
    otherwise the destroy sweep re-fires the DELETE forever (live storm,
    job 491) — but only after the confirm GET agrees the instance is gone."""
    from scribe.pipeline import whisper_client

    monkeypatch.setattr(whisper_client, "_sleep", lambda _s: None)
    calls: list[tuple[str, str]] = []

    def fake_vast(_api_key, method, path, payload=None, timeout=45, **_retry_kwargs):
        calls.append((method, path))
        raise WhisperError(f"Vast API {method} {path}: HTTP {gone_code}: no such instance")

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    whisper_client._destroy_instance("vast-test-key", 47189946)

    assert calls == [("DELETE", "/instances/47189946/"), ("GET", "/instances/47189946/")]


def test_destroy_instance_delete_404_but_confirm_shows_present_raises(monkeypatch):
    """A DELETE-404 is not trusted blindly: if the confirm still sees the
    instance (id/path anomaly), the destroy must fail so bookkeeping stays."""
    from scribe.pipeline import whisper_client

    monkeypatch.setattr(whisper_client, "_sleep", lambda _s: None)

    def fake_vast(_api_key, method, path, payload=None, timeout=45, **_retry_kwargs):
        if method == "DELETE":
            raise WhisperError(f"Vast API {method} {path}: HTTP 404: no such instance")
        return {"instances": [{"id": 777, "actual_status": "running"}]}

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    with pytest.raises(WhisperError, match="still present"):
        whisper_client._destroy_instance("vast-test-key", 777)


def test_destroy_instance_raises_when_delete_fails(monkeypatch):
    from scribe.pipeline import whisper_client

    def fake_vast(_api_key, method, path, payload=None, timeout=45, **_retry_kwargs):
        raise WhisperError("Vast API DELETE /instances/777/: HTTP 500: no")

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    with pytest.raises(WhisperError, match="HTTP 500"):
        whisper_client._destroy_instance("vast-test-key", 777)


def test_destroy_instance_raises_when_followup_still_shows_instance(monkeypatch):
    from scribe.pipeline import whisper_client

    monkeypatch.setattr(whisper_client, "_sleep", lambda _s: None)

    def fake_vast(_api_key, method, path, payload=None, timeout=45, **_retry_kwargs):
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
    # The Vast failure now surfaces through the transcription provider chain:
    # a WhisperError from the Vast provider exhausts the (default Vast-only)
    # chain, so the worker records a TranscribeChainError that preserves the
    # underlying root cause ("destroy failed").
    assert "TranscribeChainError" in job.error
    assert "destroy failed" in job.error


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


def test_process_job_reaches_done_despite_unconfirmed_destroy(db_session, monkeypatch, tmp_path):
    """#426 D2: a rate-limited teardown after a successful transcription must
    not discard the paid transcript. The job finishes; destroy bookkeeping
    stays for the sweep + reaper (both status-independent). The teardown just
    failed (fresh timestamp), so the done-guard must NOT re-fire a destroy
    into the same rate-limit window."""
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    db_session.execute(
        delete(Job).where(Job.video_id.in_(["pending:vast-done-unconf", "vast-life-video"]))
    )
    db_session.commit()

    def fake_transcribe(*_args, on_instance_created=None, on_destroy_failed=None, **_kwargs):
        on_instance_created(4242)
        on_destroy_failed(4242)
        return TranscribeResult("transcript", "en", 42, "fake", 4242, 0.01)

    _patch_worker_pipeline(monkeypatch, tmp_path, fake_transcribe)
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")

    destroy_attempts: list[int] = []

    def failing_destroy(_api_key, instance_id):
        destroy_attempts.append(instance_id)
        raise WhisperError("Vast API DELETE /instances/4242/: HTTP 429: too frequent")

    monkeypatch.setattr(worker_loop.whisper_client, "_destroy_instance", failing_destroy)

    job = Job(
        url="https://youtu.be/vast-done-unconf",
        video_id="pending:vast-done-unconf",
        status=JobStatus.downloading,
    )
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.done
    assert job.vast_instance_id == 4242
    assert job.destroy_failed_at is not None
    assert destroy_attempts == []  # fresh failure → floored, sweep owns it


def test_set_job_status_last_chance_destroy_when_ripe(db_session, monkeypatch):
    """When the last destroy failure is older than the sweep floor, marking a
    job done takes one synchronous last-chance destroy and clears the
    bookkeeping on success."""
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    db_session.execute(delete(Job).where(Job.video_id == "done-guard-ripe"))
    db_session.commit()

    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    destroy_attempts: list[int] = []
    monkeypatch.setattr(
        worker_loop.whisper_client,
        "_destroy_instance",
        lambda _api_key, instance_id: destroy_attempts.append(instance_id),
    )

    job = Job(
        url="https://youtu.be/done-guard-ripe",
        video_id="done-guard-ripe",
        status=JobStatus.summarizing,
        vast_instance_id=555,
        destroy_failed_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    db_session.add(job)
    db_session.commit()

    worker_loop._set_job_status(db_session, job, JobStatus.done)

    db_session.refresh(job)
    assert job.status == JobStatus.done
    assert job.destroy_failed_at is None
    assert destroy_attempts == [555]


def test_set_job_status_skips_last_chance_destroy_when_fresh(db_session, monkeypatch):
    """A destroy that failed seconds ago is NOT re-fired at finalize (that is
    the sweep floor's job); the job still finishes."""
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    db_session.execute(delete(Job).where(Job.video_id == "done-guard-fresh"))
    db_session.commit()

    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")

    def unexpected_destroy(_api_key, instance_id):
        raise AssertionError(f"destroy must not run for fresh failure ({instance_id})")

    monkeypatch.setattr(worker_loop.whisper_client, "_destroy_instance", unexpected_destroy)

    job = Job(
        url="https://youtu.be/done-guard-fresh",
        video_id="done-guard-fresh",
        status=JobStatus.summarizing,
        vast_instance_id=556,
        destroy_failed_at=datetime.now(UTC),
    )
    db_session.add(job)
    db_session.commit()

    worker_loop._set_job_status(db_session, job, JobStatus.done)

    db_session.refresh(job)
    assert job.status == JobStatus.done
    assert job.destroy_failed_at is not None


def test_retry_failed_vast_destroys_respects_backoff_floor(db_session, monkeypatch):
    """#426 D4: rows younger than the floor wait for a later sweep, so a
    rate-limit episode is not refreshed on every 5s worker tick. A row at
    exactly the floor age is sweep-eligible (<= boundary)."""
    from scribe.config import settings
    from scribe.worker import loop as worker_loop

    # The sweep scans the whole table: purge destroy-pending residue from
    # earlier tests in this module so counts below stay deterministic.
    db_session.execute(delete(Job).where(Job.destroy_failed_at.is_not(None)))
    db_session.execute(
        delete(Job).where(Job.video_id.in_(["floor-fresh", "floor-ripe", "floor-boundary"]))
    )
    db_session.commit()

    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    destroyed: list[int] = []
    monkeypatch.setattr(
        worker_loop.whisper_client,
        "_destroy_instance",
        lambda _api_key, instance_id: destroyed.append(instance_id),
    )

    now = datetime.now(UTC)
    fresh = Job(
        url="https://youtu.be/floor-fresh",
        video_id="floor-fresh",
        status=JobStatus.failed,
        vast_instance_id=111,
        destroy_failed_at=now,
    )
    ripe = Job(
        url="https://youtu.be/floor-ripe",
        video_id="floor-ripe",
        status=JobStatus.failed,
        vast_instance_id=222,
        destroy_failed_at=now - timedelta(seconds=120),
    )
    boundary = Job(
        url="https://youtu.be/floor-boundary",
        video_id="floor-boundary",
        status=JobStatus.failed,
        vast_instance_id=333,
        destroy_failed_at=now
        - timedelta(seconds=worker_loop._VAST_DESTROY_RETRY_FLOOR_SECONDS),
    )
    db_session.add_all([fresh, ripe, boundary])
    db_session.commit()

    assert worker_loop.retry_failed_vast_destroys(db_session) == 2

    db_session.refresh(fresh)
    db_session.refresh(ripe)
    db_session.refresh(boundary)
    assert sorted(destroyed) == [222, 333]
    assert 111 not in destroyed
    assert ripe.destroy_failed_at is None
    assert boundary.destroy_failed_at is None
    assert fresh.destroy_failed_at is not None
