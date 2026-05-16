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
from scribe.obs.live_logs import job_log_buffer


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
    short_description: str | None = None,
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
        short_description=short_description,
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


def test_production_home_serves_spa_and_classic_list_is_explicit(client, db_session):
    _seed_transcript(
        db_session,
        video_id="classic1111",
        title="Classic List Entry",
        summary_md="done",
        tags=["legacy"],
    )

    home = client.get("/")
    assert home.status_code == 200
    assert "<title>Scribe SPA</title>" in home.text
    assert '<div id="root"></div>' in home.text
    assert "Classic List Entry" not in home.text

    classic = client.get("/classic")
    assert classic.status_code == 200
    assert "Classic List Entry" in classic.text
    assert 'action="/classic"' in classic.text
    assert 'href="/classic?tag=legacy"' in classic.text


def test_api_library_happy_path_excerpts_and_paginates(client, db_session):
    _seed_transcript(
        db_session,
        video_id="libone12345",
        title="Library One",
        summary_md="# Heading\n\n**Important** `summary` text " + "x" * 260,
        short_description="Intentional fluent card description.",
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
    assert second["summary_excerpt"] == "Intentional fluent card description."
    assert second["vast_cost"] == 0.0184


def test_api_library_fallback_excerpt_uses_sentence_boundary(client, db_session):
    _seed_transcript(
        db_session,
        video_id="fallback111",
        title="Fallback Sentence",
        summary_md="# Heading\n\nFirst complete sentence. Second sentence has more detail " + "x" * 260,
    )

    row = client.get("/api/library").json()["rows"][0]

    assert row["summary_excerpt"] == "Heading First complete sentence."


def test_api_library_short_description_respects_excerpt_limit(client, db_session):
    _seed_transcript(
        db_session,
        video_id="shortlimit1",
        title="Short Description Limit",
        summary_md="Fallback should not be used.",
        short_description=(
            "Intentional first sentence for the library card. "
            "Second complete sentence keeps the excerpt fluent. "
            + "overflow " * 40
        ),
    )

    row = client.get("/api/library").json()["rows"][0]

    assert row["summary_excerpt"] == (
        "Intentional first sentence for the library card. "
        "Second complete sentence keeps the excerpt fluent."
    )
    assert len(row["summary_excerpt"]) < 240


def test_api_library_fallback_excerpt_does_not_cut_inside_word(client, db_session):
    _seed_transcript(
        db_session,
        video_id="fallback222",
        title="Fallback Word",
        summary_md="word " * 47 + "supercalifragilisticexpialidocious tail",
    )

    row = client.get("/api/library").json()["rows"][0]

    assert row["summary_excerpt"].endswith("word")
    assert "supercalifragilisticexpialidocious" not in row["summary_excerpt"]
    assert len(row["summary_excerpt"]) < 240


def test_sentence_boundary_excerpt_never_cuts_single_long_word():
    long_word = "x" * 260

    assert routes_module._sentence_boundary_excerpt(long_word) == long_word


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


def test_transcript_detail_json_keeps_full_body_for_spa(client, db_session):
    _, transcript = _seed_transcript(
        db_session,
        video_id="detailjson1",
        title="Detail JSON",
        summary_md="# Heading\n\n1. **First**\n2. `Second`",
        tags=["systems"],
        vast_cost=0.125,
    )

    resp = client.get(
        f"/transcripts/{transcript.id}",
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == transcript.id
    assert body["job_id"] == transcript.job_id
    assert body["summary_md"].startswith("# Heading")
    assert body["transcript_md"] == "transcript body"
    assert body["vast_cost"] == 0.125


def test_transcript_detail_json_allows_partial_summary(client, db_session):
    _, transcript = _seed_transcript(
        db_session,
        video_id="detailpart1",
        title="Detail Partial",
        summary_md=None,
    )

    resp = client.get(
        f"/transcripts/{transcript.id}",
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json()["summary_md"] is None


def test_transcript_detail_html_accept_redirects_to_spa(client, db_session):
    _, transcript = _seed_transcript(
        db_session,
        video_id="detailhtml1",
        title="Detail HTML",
        summary_md="done",
    )

    resp = client.get(
        f"/transcripts/{transcript.id}",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert resp.status_code == 307
    assert resp.headers["location"] == f"/#/transcript/{transcript.id}"


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


def test_get_job_includes_pipeline_stages(client, db_session):
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    job = Job(
        url="https://youtu.be/detailjob1",
        video_id="detailjob1",
        status=JobStatus.summarizing,
        created_at=now - dt.timedelta(minutes=4),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add_all(
        [
            JobStageEvent(
                job_id=job.id,
                stage="queued",
                started_at=now - dt.timedelta(minutes=4),
                finished_at=now - dt.timedelta(minutes=3),
            ),
            JobStageEvent(
                job_id=job.id,
                stage="downloading",
                started_at=now - dt.timedelta(minutes=3),
                finished_at=now - dt.timedelta(minutes=2),
            ),
            JobStageEvent(
                job_id=job.id,
                stage="transcribing",
                started_at=now - dt.timedelta(minutes=2),
                finished_at=now - dt.timedelta(minutes=1),
            ),
            JobStageEvent(job_id=job.id, stage="summarizing", started_at=now - dt.timedelta(minutes=1)),
        ]
    )
    db_session.commit()

    body = client.get(f"/jobs/{job.id}").json()
    assert body["job_id"] == job.id
    assert body["stages"]["queued"]["state"] == "done"
    assert body["stages"]["transcribing"]["duration_s"] == 60
    assert body["stages"]["summarizing"]["state"] == "active"
    assert body["stages"]["done"]["state"] == "pending"


def test_admin_cancel_marks_active_job_failed_and_removes_from_active(client, db_session):
    job = Job(url="https://youtu.be/canceljob1", video_id="canceljob1", status=JobStatus.transcribing)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/admin/jobs/{job.id}/cancel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "cancelled by operator"
    assert body["stages"]["transcribing"]["state"] == "failed"

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert job.error == "cancelled by operator"
    active = client.get("/api/jobs/active").json()
    assert active == {"jobs": []}


def test_admin_cancel_rejects_terminal_job(client, db_session):
    job = Job(url="https://youtu.be/canceldone1", video_id="canceldone1", status=JobStatus.done)
    db_session.add(job)
    db_session.commit()

    resp = client.post(f"/admin/jobs/{job.id}/cancel")
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]

    db_session.refresh(job)
    assert job.status == JobStatus.done
    assert job.error is None


def test_api_jobs_recent_failures(client, db_session):
    job = Job(
        url="https://youtu.be/failurejob1",
        video_id="failurejob1",
        status=JobStatus.failed,
        error="whisper failed",
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(JobStageEvent(job_id=job.id, stage="transcribing", started_at=dt.datetime.now(dt.UTC)))
    db_session.commit()

    body = client.get("/api/jobs/recent-failures").json()
    assert len(body["jobs"]) == 1
    row = body["jobs"][0]
    assert row["id"] == job.id
    assert row["error"] == "whisper failed"
    assert row["stages"]["transcribing"]["state"] == "failed"


def test_api_job_log_stream_returns_buffered_worker_lines_and_closes(client, db_session):
    job = Job(url="https://youtu.be/logstream1", video_id="logstream1", status=JobStatus.done)
    db_session.add(job)
    db_session.commit()
    job_log_buffer.clear()
    job_log_buffer.append({"ts": "2026-05-16T12:00:00+00:00", "job_id": job.id, "stage": "whisper", "msg": "whisper done"})
    job_log_buffer.append({"ts": "2026-05-16T12:00:01+00:00", "job_id": job.id + 1, "stage": "done", "msg": "other"})
    job_log_buffer.append({"ts": "2026-05-16T12:00:02+00:00", "job_id": job.id, "stage": "done", "msg": "job done"})

    with client.stream("GET", f"/api/jobs/{job.id}/log/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    assert "whisper done" in body
    assert "job done" in body
    assert "other" not in body
    assert job_log_buffer.snapshot(job.id) == (0, [])
    job_log_buffer.clear()


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
    assert body["recent_failures"] == []


def test_api_ops_happy_path(client, db_session, tmp_path, monkeypatch):
    path = Path(tmp_path / "_last_success_ts")
    path.write_text(str(int(time.time())))
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 2.0)
    now = dt.datetime.now(dt.UTC)
    active = Job(url="https://youtu.be/opsactive1", video_id="opsactive1", status=JobStatus.queued)
    failed = Job(
        url="https://youtu.be/opsfailed1",
        video_id="opsfailed1",
        status=JobStatus.failed,
        error="codex exited 1",
    )
    db_session.add(active)
    db_session.add(failed)
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
    assert body["jobs_by_status"]["failed"] == 1
    assert body["queue_depth"] == 1
    assert body["transcripts_done"] == 1
    assert body["transcripts_partial"] == 1
    assert body["vast_spend_24h"] == 0.75
    assert body["daily_spend_cap_usd"] == 2.0
    assert body["backup"]["stale"] is False
    assert body["recent_failures"][0]["id"] == failed.id
    assert body["recent_failures"][0]["error"] == "codex exited 1"
