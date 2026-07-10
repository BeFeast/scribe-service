"""Worker archiving pipeline for uploaded sources (#408).

Drives the real ``process_job`` / archive-sweep / recovery code with the yt-dlp,
whisper, summarizer, ffmpeg-transcode and R2-upload seams mocked, so the upload
branch + archiving orchestration is exercised end-to-end without GPUs, network,
or ffmpeg. Needs SCRIBE_TEST_DATABASE_URL (Postgres enum + FOR UPDATE).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.api.auth import Actor
from scribe.config import settings
from scribe.db.models import Job, JobStageEvent, JobStatus, Transcript
from scribe.main import app
from scribe.pipeline import ffmpeg, media_store, uploads
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.summary_validator import SummaryResult
from scribe.worker import loop as worker_loop


def _fake_transcribe_result():
    return SimpleNamespace(
        transcript_md="## Transcript\n\nhello world",
        detected_language="en",
        duration_seconds=61.0,
        vast_cost=0.0,
        provider="local-whisper",
    )


def _patch_common(monkeypatch, *, has_video: bool, upload_calls: list, transcode_calls: list, upload_raises=False):
    monkeypatch.setattr(worker_loop.ffmpeg, "probe_media", lambda _p: ffmpeg.MediaProbe(
        has_video=has_video, has_audio=True, duration_seconds=61,
    ))
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda src, dest: dest)
    monkeypatch.setattr(worker_loop.transcribe_providers, "build_provider_chain", lambda **_k: None)
    monkeypatch.setattr(
        worker_loop.transcribe_providers, "transcribe_with_chain",
        lambda *_a, **_k: _fake_transcribe_result(),
    )
    monkeypatch.setattr(
        worker_loop.summarizer, "summarize",
        lambda *_a, **_k: SummaryResult(summary_md="## TL;DR\n- point", tags=["topic"], short_description="d"),
    )

    def fake_video(src, dest):
        transcode_calls.append(("video", dest))
        dest.write_bytes(b"fake-mp4-bytes")
        return dest

    def fake_audio(src, dest):
        transcode_calls.append(("audio", dest))
        dest.write_bytes(b"fake-opus-bytes")
        return dest

    monkeypatch.setattr(worker_loop.ffmpeg, "transcode_archival_video", fake_video)
    monkeypatch.setattr(worker_loop.ffmpeg, "transcode_archival_audio", fake_audio)
    monkeypatch.setattr(worker_loop.media_store, "is_configured", lambda: True)

    def fake_upload(path, key, content_type):
        if upload_raises:
            raise media_store.MediaStoreError("R2 outage")
        upload_calls.append((str(path), key, content_type))

    monkeypatch.setattr(worker_loop.media_store, "upload_file", fake_upload)


def _make_upload_job(session, tmp_path, monkeypatch, *, video_id: str, filename: str = "clip.mp4") -> Job:
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    job = Job(url=f"upload:{filename}", video_id=video_id, status=JobStatus.downloading, source="upload")
    session.add(job)
    session.flush()
    source = uploads.job_dir(job.id) / filename
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"original upload bytes")
    session.commit()
    return job


def test_upload_video_job_archives_to_r2(db_session, tmp_path, monkeypatch):
    upload_calls: list = []
    transcode_calls: list = []
    _patch_common(monkeypatch, has_video=True, upload_calls=upload_calls, transcode_calls=transcode_calls)
    job = _make_upload_job(db_session, tmp_path, monkeypatch, video_id="upload:aaaa111122223333")
    job_id = job.id

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job_id).one()
    assert job.status == JobStatus.done
    assert transcript.summary_md is not None
    assert transcript.media_object_key == f"media/upload_aaaa111122223333/{transcript.id}.mp4"
    assert transcript.media_content_type == "video/mp4"
    assert transcript.media_size_bytes == len(b"fake-mp4-bytes")
    assert transcript.media_uploaded_at is not None
    assert transcript.media_error is None
    # Video transcode ran; R2 got exactly one object.
    assert [c[0] for c in transcode_calls] == ["video"]
    assert len(upload_calls) == 1
    # Source (and all local remnants) cleaned up.
    assert uploads.find_source(job_id) is None
    # Archiving stage event recorded (observability), finished.
    ev = db_session.query(JobStageEvent).filter_by(job_id=job_id, stage="archiving").one()
    assert ev.finished_at is not None


def test_upload_audio_only_archives_as_opus(db_session, tmp_path, monkeypatch):
    upload_calls: list = []
    transcode_calls: list = []
    _patch_common(monkeypatch, has_video=False, upload_calls=upload_calls, transcode_calls=transcode_calls)
    job = _make_upload_job(db_session, tmp_path, monkeypatch, video_id="upload:bbbb111122223333", filename="song.mp3")
    job_id = job.id

    worker_loop.process_job(db_session, job)

    transcript = db_session.query(Transcript).filter_by(job_id=job_id).one()
    assert job.status == JobStatus.done
    assert transcript.media_content_type == "audio/ogg"
    assert transcript.media_object_key.endswith(".opus")
    # No video stage attempted for audio-only input.
    assert [c[0] for c in transcode_calls] == ["audio"]


def test_r2_outage_soft_fails_but_transcript_survives(db_session, tmp_path, monkeypatch):
    upload_calls: list = []
    transcode_calls: list = []
    _patch_common(
        monkeypatch, has_video=True, upload_calls=upload_calls,
        transcode_calls=transcode_calls, upload_raises=True,
    )
    job = _make_upload_job(db_session, tmp_path, monkeypatch, video_id="upload:cccc111122223333")
    job_id = job.id

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job_id).one()
    # Job does NOT hard-fail; transcript + summary remain available.
    assert job.status == JobStatus.done
    assert transcript.summary_md is not None
    # Archive soft-failed: error recorded, no object key.
    assert transcript.media_object_key is None
    assert "R2 outage" in transcript.media_error
    # Source is KEPT for the retry sweep.
    assert uploads.find_source(job_id) is not None


def test_retry_pending_archives_recovers_after_outage(db_session, tmp_path, monkeypatch):
    upload_calls: list = []
    transcode_calls: list = []
    # First run: R2 is down → soft failure.
    _patch_common(
        monkeypatch, has_video=True, upload_calls=upload_calls,
        transcode_calls=transcode_calls, upload_raises=True,
    )
    job = _make_upload_job(db_session, tmp_path, monkeypatch, video_id="upload:dddd111122223333")
    job_id = job.id
    worker_loop.process_job(db_session, job)
    transcript = db_session.query(Transcript).filter_by(job_id=job_id).one()
    assert transcript.media_error is not None

    # R2 recovers; the sweep re-archives from the retained source.
    ok_uploads: list = []
    _patch_common(monkeypatch, has_video=True, upload_calls=ok_uploads, transcode_calls=[])
    recovered = worker_loop.retry_pending_archives(db_session)

    assert recovered == 1
    db_session.refresh(transcript)
    assert transcript.media_object_key is not None
    assert transcript.media_error is None
    assert len(ok_uploads) == 1
    assert uploads.find_source(job_id) is None


def test_recover_interrupted_archiving_source_present(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(worker_loop.media_store, "is_configured", lambda: True)
    job = Job(url="upload:x.mp4", video_id="upload:eeee111122223333", status=JobStatus.archiving, source="upload")
    db_session.add(job)
    db_session.flush()
    transcript = Transcript(
        job_id=job.id, video_id=job.video_id, title="clip",
        transcript_md="## Transcript\n\nbody", summary_md="## TL;DR\n- p",
    )
    db_session.add(transcript)
    # Source still on disk after the "restart".
    src = uploads.job_dir(job.id) / "x.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"src")
    db_session.commit()

    worker_loop.recover_interrupted_jobs(db_session)

    db_session.refresh(job)
    db_session.refresh(transcript)
    assert job.status == JobStatus.done
    assert "pending retry" in transcript.media_error
    assert uploads.find_source(job.id) is not None


def test_recover_interrupted_archiving_source_lost(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(worker_loop.media_store, "is_configured", lambda: True)
    job = Job(url="upload:y.mp4", video_id="upload:ffff111122223333", status=JobStatus.archiving, source="upload")
    db_session.add(job)
    db_session.flush()
    transcript = Transcript(
        job_id=job.id, video_id=job.video_id, title="clip",
        transcript_md="## Transcript\n\nbody", summary_md="## TL;DR\n- p",
    )
    db_session.add(transcript)
    db_session.commit()  # no source dir created → lost after restart

    worker_loop.recover_interrupted_jobs(db_session)

    db_session.refresh(job)
    db_session.refresh(transcript)
    assert job.status == JobStatus.done
    assert transcript.media_error.endswith("(permanent)")


def test_url_job_does_not_archive(db_session, tmp_path, monkeypatch):
    upload_calls: list = []
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(
        worker_loop.downloader, "download_audio",
        lambda *_a, **_k: DownloadResult(
            audio_path=tmp_path / "a.m4a", title="yt video",
            video_id="youtube:zzz", duration_seconds=30,
        ),
    )
    (tmp_path / "a.m4a").write_bytes(b"audio")
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda src, dest: dest)
    monkeypatch.setattr(worker_loop.transcribe_providers, "build_provider_chain", lambda **_k: None)
    monkeypatch.setattr(
        worker_loop.transcribe_providers, "transcribe_with_chain",
        lambda *_a, **_k: _fake_transcribe_result(),
    )
    monkeypatch.setattr(
        worker_loop.summarizer, "summarize",
        lambda *_a, **_k: SummaryResult(summary_md="s", tags=["t"], short_description="d"),
    )
    monkeypatch.setattr(worker_loop.media_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        worker_loop.media_store, "upload_file",
        lambda *_a, **_k: upload_calls.append(_a),
    )

    job = Job(url="https://youtu.be/zzz", video_id="pending:zzz", status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()

    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job.id).one()
    assert job.status == JobStatus.done
    # URL jobs never archive: no R2 upload, no media columns, no archiving event.
    assert upload_calls == []
    assert transcript.media_object_key is None
    assert db_session.query(JobStageEvent).filter_by(job_id=job.id, stage="archiving").first() is None


# --- media retrieval endpoint ------------------------------------------------

def _admin_actor() -> Actor:
    return Actor(kind="clerk", role="admin", subject="admin_x", user_id=1, owner_id=1)


@pytest.fixture()
def media_client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "media_s3_endpoint", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(settings, "media_s3_bucket", "scribe-media")
    monkeypatch.setattr(settings, "media_s3_access_key", "AKIAFAKE")
    monkeypatch.setattr(settings, "media_s3_secret_key", "secretfake")

    def _use_session():
        yield db_session

    app.dependency_overrides[routes_module.get_session] = _use_session
    app.dependency_overrides[routes_module.require_actor] = _admin_actor
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        app.dependency_overrides.pop(routes_module.require_actor, None)


def _persist_transcript(session, *, media_key: str | None) -> int:
    job = Job(url="upload:m.mp4", video_id="upload:1111aaaa2222bbbb", status=JobStatus.done, source="upload")
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id, video_id=job.video_id, title="clip",
        transcript_md="## Transcript\n\nbody", summary_md="## TL;DR\n- p",
        media_object_key=media_key,
        media_content_type="video/mp4" if media_key else None,
    )
    session.add(transcript)
    session.commit()
    return transcript.id


def test_media_endpoint_redirects_to_presigned_url(media_client, db_session):
    tid = _persist_transcript(db_session, media_key="media/upload_x/9.mp4")
    r = media_client.get(f"/transcripts/{tid}/media", follow_redirects=False)
    assert r.status_code == 302
    assert "scribe-media/media/upload_x/9.mp4" in r.headers["location"]
    assert "X-Amz-Signature=" in r.headers["location"]


def test_media_endpoint_404_when_no_media(media_client, db_session):
    tid = _persist_transcript(db_session, media_key=None)
    r = media_client.get(f"/transcripts/{tid}/media", follow_redirects=False)
    assert r.status_code == 404


def test_media_endpoint_rejects_other_owner(db_session, monkeypatch):
    tid = _persist_transcript(db_session, media_key="media/upload_x/9.mp4")

    def _use_session():
        yield db_session

    # A non-admin actor whose owner_id does not match the transcript owner.
    other = Actor(kind="clerk", role="user", subject="other", user_id=2, owner_id=999)
    app.dependency_overrides[routes_module.get_session] = _use_session
    app.dependency_overrides[routes_module.require_actor] = lambda: other
    try:
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get(f"/transcripts/{tid}/media", follow_redirects=False)
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        app.dependency_overrides.pop(routes_module.require_actor, None)
