"""Keep production acceptance jobs isolated from existing transcripts and shares."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from scribe.api.routes import get_session
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript, TranscriptShareLink
from scribe.main import app
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.summary_validator import SummaryResult
from scribe.pipeline.whisper_client import TranscribeResult
from scribe.worker import loop

VIDEO_ID = "fresh123456"
URL = f"https://youtu.be/{VIDEO_ID}"
RUN_ID = "scribe-verify-12345678-1234-1234-1234-123456789abc"
HEADERS = {"X-Request-ID": RUN_ID}
JOBS = "/admin/acceptance/jobs"


@pytest.fixture()
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 0)
    monkeypatch.setitem(app.dependency_overrides, get_session, lambda: db_session)
    with TestClient(app) as test_client:
        yield test_client
    db_session.execute(delete(Job).where(Job.video_id == VIDEO_ID))
    db_session.commit()


def _create_fixture(client, session):
    response = client.post(JOBS, json={"url": URL}, headers=HEADERS)
    assert response.status_code == 201, response.text
    job = session.get(Job, response.json()["job_id"])
    assert job.acceptance_run_id == job.correlation_id == RUN_ID
    assert not job.notify
    return job


def _add_partial(session, job):
    transcript = Transcript(job_id=job.id, video_id=VIDEO_ID, title="Existing", transcript_md="Existing speech")
    session.add(transcript)
    session.commit()
    return transcript


def test_acceptance_create_rejects_existing_partial(client, db_session):
    other_job = Job(url=URL, video_id=VIDEO_ID, status=JobStatus.failed)
    db_session.add(other_job)
    db_session.flush()
    partial = _add_partial(db_session, other_job)

    denied = client.post(JOBS, json={"url": URL}, headers=HEADERS)
    assert denied.status_code == 409
    assert db_session.scalars(select(Job).where(Job.video_id == VIDEO_ID)).all() == [other_job]
    assert partial.job_id == other_job.id


@pytest.mark.parametrize("race_before_download", [True, False])
def test_acceptance_worker_never_reattaches_a_concurrent_partial(
    client, db_session, monkeypatch, tmp_path, race_before_download,
):
    fixture_job = _create_fixture(client, db_session)
    other_job = Job(url=URL, video_id=VIDEO_ID, status=JobStatus.failed)
    db_session.add(other_job)
    db_session.flush()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixture")
    if race_before_download:
        _add_partial(db_session, other_job)
        assert client.get(f"/jobs/{fixture_job.id}").json()["transcript"] is None

    def download(*_args, **_kwargs):
        if not race_before_download:
            _add_partial(db_session, other_job)
        return DownloadResult(audio_path=audio, title="Fixture", video_id=VIDEO_ID, duration_seconds=19)

    monkeypatch.setattr(loop.downloader, "download_audio", download)
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(loop.ffmpeg, "to_wav_16k_mono", lambda *_a, **_kw: audio)
    monkeypatch.setattr(loop.whisper_client, "transcribe", lambda *_a, **_kw: TranscribeResult(
        transcript_md="## Transcript\n\nFixture speech", detected_language="en", duration_seconds=19,
        backend="test", vast_instance_id=0, vast_cost=0,
    ))
    monkeypatch.setattr(loop.summarizer, "summarize", lambda *_a, **_kw: SummaryResult(
        summary_md="## Summary\n\nFixture summary", tags=[], short_description="Fixture",
    ))
    fixture_job.status = JobStatus.downloading
    db_session.commit()
    loop.process_job(db_session, fixture_job)
    db_session.expire_all()
    assert fixture_job.status == JobStatus.done
    assert fixture_job.transcript.acceptance_run_id == RUN_ID
    assert fixture_job.transcript.transcript_md.endswith("Fixture speech")
    assert other_job.transcript.transcript_md == "Existing speech"
    assert client.get(f"/jobs/{other_job.id}").json()["transcript"]["id"] == other_job.transcript.id

    transcript_id = fixture_job.transcript.id
    share = client.post(f"/api/transcripts/{transcript_id}/share-links", json={
        "target_kind": "ai_transcript", "label": RUN_ID,
    }).json()
    cleanup = f"{JOBS}/{fixture_job.id}?share_id={share['id']}"
    assert client.delete(cleanup, headers=HEADERS).status_code == 412
    assert client.post(f"/api/share-links/{share['id']}/revoke").status_code == 200
    assert client.delete(cleanup, headers=HEADERS).status_code == 204
    assert db_session.get(Transcript, transcript_id) is None
    assert db_session.get(TranscriptShareLink, share["id"]) is None
    assert other_job.transcript.transcript_md == "Existing speech"


def test_acceptance_cleanup_refuses_foreign_children(client, db_session):
    fixture_job = _create_fixture(client, db_session)
    fixture_job.status = JobStatus.failed
    partial = _add_partial(db_session, fixture_job)
    cleanup = f"{JOBS}/{fixture_job.id}"

    assert client.delete(cleanup, headers=HEADERS).status_code == 412
    assert db_session.get(Transcript, partial.id) is not None
    partial.acceptance_run_id = RUN_ID
    db_session.commit()
    link = client.post(f"/api/transcripts/{partial.id}/share-links", json={
        "target_kind": "ai_transcript", "label": "normal link",
    }).json()
    assert client.post(f"/api/share-links/{link['id']}/revoke").status_code == 200
    assert client.delete(f"{cleanup}?share_id={link['id']}", headers=HEADERS).status_code == 412
    assert db_session.get(TranscriptShareLink, link["id"]) is not None
