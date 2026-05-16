"""DB-coupled tests for SPA JSON list endpoints."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import Job, JobStageEvent, JobStatus, Transcript
from scribe.main import app


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    db_session.execute(delete(Job))
    db_session.commit()
    yield
    db_session.execute(delete(Job))
    db_session.commit()


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)


def _seed_transcript(
    session,
    *,
    video_id: str,
    title: str,
    summary_md: str | None,
    tags: list[str] | None = None,
    created_at: dt.datetime | None = None,
    vast_cost: float | None = None,
):
    job = Job(url=f"https://youtu.be/{video_id}", video_id=video_id, status=JobStatus.done)
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id,
        video_id=video_id,
        title=title,
        transcript_md="transcript body",
        summary_md=summary_md,
        tags=tags,
        duration_seconds=123,
        lang="en",
        vast_cost=vast_cost,
        summary_shortlink="https://go.example/s",
        transcript_shortlink="https://go.example/t",
    )
    if created_at is not None:
        transcript.created_at = created_at
    session.add(transcript)
    session.commit()
    return job, transcript


def test_api_library_empty(client):
    resp = client.get("/api/library")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {"rows": [], "total": 0, "limit": 50, "offset": 0}


def test_api_library_happy_path_excerpts_and_paginates(client, db_session):
    _seed_transcript(
        db_session,
        video_id="libone12345",
        title="Library One",
        summary_md="# Heading\n\n**Important** `summary` text " + "x" * 260,
        tags=["systems"],
        vast_cost=0.0184,
    )
    _seed_transcript(
        db_session,
        video_id="libtwo12345",
        title="Library Two",
        summary_md=None,
        tags=["draft"],
    )

    resp = client.get("/api/library", params={"limit": 1, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert len(body["rows"]) == 1
    assert body["rows"][0]["is_partial"] is True
    assert "summary_md" not in body["rows"][0]

    second = client.get("/api/library", params={"limit": 1, "offset": 1}).json()["rows"][0]
    assert second["title"] == "Library One"
    assert second["summary_excerpt"].startswith("Heading Important summary text")
    assert len(second["summary_excerpt"]) == 240
    assert second["vast_cost"] == 0.0184


def test_api_library_filters_q_and_tag(client, db_session):
    _seed_transcript(
        db_session,
        video_id="filter11111",
        title="Systems Talk",
        summary_md="alpha beta",
        tags=["systems"],
    )
    _seed_transcript(
        db_session,
        video_id="filter22222",
        title="Cooking Talk",
        summary_md="needle in summary",
        tags=["food"],
    )

    by_summary = client.get("/api/library", params={"q": "needle"}).json()
    assert by_summary["total"] == 1
    assert by_summary["rows"][0]["video_id"] == "filter22222"

    by_tag = client.get("/api/library", params={"tag": "systems"}).json()
    assert by_tag["total"] == 1
    assert by_tag["rows"][0]["video_id"] == "filter11111"


def test_api_jobs_active_empty(client):
    resp = client.get("/api/jobs/active")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {"jobs": []}


def test_api_jobs_active_happy_path(client, db_session):
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    job = Job(
        url="https://youtu.be/activejob1",
        video_id="activejob1",
        status=JobStatus.transcribing,
        source="telegram",
        created_at=now - dt.timedelta(minutes=3),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add_all(
        [
            JobStageEvent(
                job_id=job.id,
                stage="queued",
                started_at=now - dt.timedelta(minutes=3),
                finished_at=now - dt.timedelta(minutes=2),
            ),
            JobStageEvent(
                job_id=job.id,
                stage="downloading",
                started_at=now - dt.timedelta(minutes=2),
                finished_at=now - dt.timedelta(minutes=1),
            ),
            JobStageEvent(job_id=job.id, stage="transcribing", started_at=now - dt.timedelta(minutes=1)),
        ]
    )
    transcript = Transcript(
        job_id=job.id,
        video_id=job.video_id,
        title="Active title",
        transcript_md="partial",
        summary_md=None,
    )
    db_session.add(transcript)
    db_session.commit()

    body = client.get("/api/jobs/active").json()
    assert len(body["jobs"]) == 1
    row = body["jobs"][0]
    assert row["id"] == job.id
    assert row["title"] == "Active title"
    assert row["status"] == "transcribing"
    assert row["stages"]["queued"]["state"] == "done"
    assert row["stages"]["downloading"]["duration_s"] == 60
    assert row["stages"]["transcribing"]["state"] == "active"
    assert row["stages"]["summarizing"]["state"] == "pending"


def test_api_ops_empty(client, tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    resp = client.get("/api/ops")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert body["window_days"] == 1
    assert body["jobs_by_status"] == {}
    assert body["queue_depth"] == 0
    assert body["transcripts_done"] == 0
    assert body["transcripts_partial"] == 0
    assert len(body["spend_series_14d"]) == 14
    assert body["backup"]["stale"] is True


def test_api_ops_happy_path(client, db_session, tmp_path, monkeypatch):
    path = Path(tmp_path / "_last_success_ts")
    path.write_text(str(int(time.time())))
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 2.0)
    now = dt.datetime.now(dt.UTC)
    active = Job(url="https://youtu.be/opsactive1", video_id="opsactive1", status=JobStatus.queued)
    db_session.add(active)
    _seed_transcript(
        db_session,
        video_id="opsdone1111",
        title="Done",
        summary_md="done",
        tags=["ops"],
        created_at=now,
        vast_cost=0.25,
    )
    _seed_transcript(
        db_session,
        video_id="opspart1111",
        title="Partial",
        summary_md=None,
        created_at=now,
        vast_cost=0.5,
    )

    body = client.get("/api/ops").json()
    assert body["jobs_by_status"]["queued"] == 1
    assert body["queue_depth"] == 1
    assert body["transcripts_done"] == 1
    assert body["transcripts_partial"] == 1
    assert body["vast_spend_24h"] == 0.75
    assert body["daily_spend_cap_usd"] == 2.0
    assert body["backup"]["stale"] is False
