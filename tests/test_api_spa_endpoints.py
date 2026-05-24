"""DB-coupled tests for SPA JSON list endpoints."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import Job, JobStageEvent, JobStatus, Owner, Transcript, TranscriptShareLink, User
from scribe.main import app
from scribe.obs.live_logs import job_log_buffer


@pytest.fixture(autouse=True)
def clean_tables(db_session):
    db_session.execute(delete(TranscriptShareLink))
    db_session.execute(delete(Job))
    db_session.execute(delete(User))
    db_session.execute(delete(Owner))
    db_session.commit()
    yield
    db_session.execute(delete(TranscriptShareLink))
    db_session.execute(delete(Job))
    db_session.execute(delete(User))
    db_session.execute(delete(Owner))
    db_session.commit()


@pytest.fixture()
def client(db_session):
    old_token = settings.config_api_bearer_token
    settings.config_api_bearer_token = ""
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)
    settings.config_api_bearer_token = old_token


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
    url: str | None = None,
    owner_subject: str | None = None,
    owner_email: str | None = None,
):
    job = Job(
        url=url or f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.done,
        title=title,
        owner_subject=owner_subject,
        owner_email=owner_email,
    )
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
        owner_subject=owner_subject,
        owner_email=owner_email,
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


def test_production_spa_deep_links_serve_shell(client):
    for route in ("/queue", "/ops", "/settings"):
        resp = client.get(route)
        assert resp.status_code == 200
        assert "<title>Scribe SPA</title>" in resp.text
        assert '<div id="root"></div>' in resp.text


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


def test_api_library_includes_provider_aware_source_link_for_x(client, db_session):
    _seed_transcript(
        db_session,
        video_id="xlib123",
        title="X Library",
        summary_md="done",
        url="https://x.com/example/status/123",
    )

    row = client.get("/api/library").json()["rows"][0]

    assert row["source_label"] == "Twitter/X"
    assert row["source_url"] == "https://x.com/example/status/123"


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


def test_api_library_filters_to_current_owner(client, db_session):
    _seed_transcript(
        db_session,
        video_id="ownerlib111",
        title="Mine",
        summary_md="mine",
        owner_subject="owner-current",
        owner_email="current@example.test",
    )
    _seed_transcript(
        db_session,
        video_id="ownerlib222",
        title="Other",
        summary_md="other",
        owner_subject="owner-other",
        owner_email="other@example.test",
    )

    resp = client.get(
        "/api/library",
        headers={"Authorization": "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJvd25lci1jdXJyZW50In0."},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["video_id"] == "ownerlib111"


def test_api_library_shows_default_owner_backfilled_rows(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "default_owner_subject", "default-owner")
    monkeypatch.setattr(settings, "default_owner_email", "default@example.test")
    monkeypatch.setattr(settings, "machine_bearer_token", "machine-token")
    _seed_transcript(
        db_session,
        video_id="backfill111",
        title="Backfilled",
        summary_md="visible",
        owner_subject="default-owner",
        owner_email="default@example.test",
    )
    _seed_transcript(
        db_session,
        video_id="backfill222",
        title="Other",
        summary_md="hidden",
        owner_subject="other-owner",
    )

    resp = client.get("/api/library", headers={"Authorization": "Bearer machine-token"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["video_id"] == "backfill111"


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


def test_api_jobs_active_uses_job_title_before_transcript_exists(client, db_session):
    job = Job(
        url="https://youtu.be/titleearly1",
        video_id="titleearly1",
        status=JobStatus.transcribing,
        source="manual",
        title="Early Video Title",
    )
    db_session.add(job)
    db_session.commit()

    body = client.get("/api/jobs/active").json()
    assert body["jobs"][0]["title"] == "Early Video Title"


def test_get_job_includes_pipeline_stages(client, db_session):
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    job = Job(
        url="https://youtu.be/detailjob1",
        video_id="detailjob1",
        status=JobStatus.summarizing,
        title="Detail job title",
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
    assert body["title"] == "Detail job title"
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


def test_admin_delete_transcript_removes_owning_job(client, db_session):
    job, transcript = _seed_transcript(
        db_session,
        video_id="deleteone1",
        title="Delete Me",
        summary_md="done",
        tags=["cleanup"],
    )

    resp = client.delete(f"/admin/transcripts/{transcript.id}")
    assert resp.status_code == 204, resp.text

    assert db_session.get(Transcript, transcript.id) is None
    assert db_session.get(Job, job.id) is None
    assert client.get("/api/library").json()["total"] == 0


def test_admin_delete_transcript_rejects_active_job(client, db_session):
    job, transcript = _seed_transcript(
        db_session,
        video_id="activekeep1",
        title="Keep Active",
        summary_md="done",
    )
    job.status = JobStatus.transcribing
    db_session.commit()

    resp = client.delete(f"/admin/transcripts/{transcript.id}")
    assert resp.status_code == 409
    assert "active" in resp.json()["detail"]

    assert db_session.get(Transcript, transcript.id) is not None
    assert db_session.get(Job, job.id) is not None


def test_admin_delete_job_dismisses_failed_job(client, db_session):
    job = Job(
        url="https://youtu.be/clearfailed1",
        video_id="clearfailed1",
        status=JobStatus.failed,
        error="test failure",
    )
    db_session.add(job)
    db_session.commit()

    resp = client.delete(f"/admin/jobs/{job.id}")
    assert resp.status_code == 204, resp.text

    assert db_session.scalar(select(Job).where(Job.id == job.id)) is None
    assert client.get("/api/jobs/recent-failures").json() == {"jobs": []}


def test_admin_delete_job_rejects_non_failed_job(client, db_session):
    job = Job(url="https://youtu.be/keepdone1", video_id="keepdone1", status=JobStatus.done)
    db_session.add(job)
    db_session.commit()

    resp = client.delete(f"/admin/jobs/{job.id}")
    assert resp.status_code == 409
    assert "only failed jobs" in resp.json()["detail"]
    assert db_session.get(Job, job.id) is not None


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

    def fail_rollcall():
        raise AssertionError("blocking rollcall must not run in /api/ops")

    monkeypatch.setattr("scribe.api.routes.ops_helpers._system_rollcall", fail_rollcall)
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
    assert [row["label"] for row in body["system"]] == [
        "scribe-service",
        "Worker",
        "Postgres",
        "Backup",
        "Vast.ai",
        "codex CLI",
    ]


def test_api_ops_happy_path(client, db_session, tmp_path, monkeypatch):
    path = Path(tmp_path / "_last_success_ts")
    path.write_text(str(int(time.time())))
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "daily_spend_cap_usd", 2.0)

    def fail_rollcall():
        raise AssertionError("blocking rollcall must not run in /api/ops")

    monkeypatch.setattr("scribe.api.routes.ops_helpers._system_rollcall", fail_rollcall)
    now = dt.datetime.now(dt.UTC)
    active = Job(url="https://youtu.be/opsactive1", video_id="opsactive1", status=JobStatus.queued)
    failed = Job(
        url="https://youtu.be/opsfailed1",
        video_id="opsfailed1",
        status=JobStatus.failed,
        error="codex exited 1",
    )
    stale_failed = Job(
        url="https://youtu.be/opsfailedold",
        video_id="opsfailedold",
        status=JobStatus.failed,
        error="old failure",
        created_at=now - dt.timedelta(days=8),
        updated_at=now - dt.timedelta(days=8),
    )
    db_session.add(active)
    db_session.add(failed)
    db_session.add(stale_failed)
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
    assert [item["video_id"] for item in body["recent_failures"]] == ["opsfailed1"]
    assert next(row for row in body["system"] if row["label"] == "Postgres") == {
        "label": "Postgres",
        "value": "request query succeeded",
        "status": "ok",
    }


def test_api_ops_worker_rollcall_warns_on_unclamped_active_count(client, db_session, tmp_path, monkeypatch):
    path = Path(tmp_path / "_last_success_ts")
    path.write_text(str(int(time.time())))
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "worker_concurrency", 2)

    jobs = [
        Job(url=f"https://youtu.be/opsbusy{i}", video_id=f"opsbusy{i}", status=JobStatus.transcribing)
        for i in range(3)
    ]
    db_session.add_all(jobs)
    db_session.commit()

    body = client.get("/api/ops").json()
    assert body["worker_pool"] == {"active": 2, "total": 2}
    assert next(row for row in body["system"] if row["label"] == "Worker") == {
        "label": "Worker",
        "value": "3/2 busy",
        "status": "warn",
    }


def _auth_headers(subject: str, email: str | None = None) -> dict[str, str]:
    return {
        "x-scribe-test-clerk-sub": subject,
        "x-scribe-test-email": email or f"{subject}@example.test",
    }


def _seed_user(session, *, subject: str, email: str | None = None, role: str = "user") -> User:
    normalized_email = email or f"{subject}@example.test"
    owner = Owner(display_name=normalized_email)
    user = User(
        owner=owner,
        clerk_subject=subject,
        primary_email=normalized_email,
        display_name=normalized_email,
        role=role,
    )
    session.add(user)
    session.commit()
    return user


def test_direct_transcript_endpoints_require_auth(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    _, transcript = _seed_transcript(
        db_session,
        video_id="private1111",
        title="Private",
        summary_md="private summary",
    )
    remote_headers = {"x-forwarded-for": "203.0.113.10"}

    detail = client.get(f"/transcripts/{transcript.id}", headers=remote_headers)
    summary = client.get(f"/transcripts/{transcript.id}/summary.md", headers=remote_headers)
    raw = client.get(f"/transcripts/{transcript.id}/transcript.md", headers=remote_headers)

    assert detail.status_code == 401
    assert summary.status_code == 401
    assert raw.status_code == 401
    assert "private summary" not in detail.text
    assert "private summary" not in summary.text
    assert "transcript body" not in raw.text


def test_share_token_serves_summary_without_direct_auth(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    _seed_user(db_session, subject="user_a")
    _, transcript = _seed_transcript(
        db_session,
        video_id="share111111",
        title="Shared",
        summary_md="shared summary",
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )

    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={"target_kind": "summary_markdown", "label": "reader"},
    )
    assert created.status_code == 201, created.text
    share_url = created.json()["share_url"]
    token = created.json()["token"]
    assert f"/transcripts/{transcript.id}" not in share_url

    direct = client.get(
        f"/transcripts/{transcript.id}/summary.md",
        headers={"x-forwarded-for": "203.0.113.10"},
    )
    shared = client.get(f"/share/{token}")

    assert direct.status_code == 401
    assert shared.status_code == 200
    assert shared.headers["content-type"].startswith("text/markdown")
    assert shared.text == "shared summary"


def test_share_page_sanitizes_rendered_summary_html(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, subject="user_a")
    _, transcript = _seed_transcript(
        db_session,
        video_id="sharexss111",
        title="Shared <Title>",
        summary_md=(
            "Safe **summary**\n\n"
            "<script>alert('xss')</script>\n\n"
            "<img src=x onerror=alert(1)>\n\n"
            "[bad](javascript:alert(1))"
        ),
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )
    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={"target_kind": "page"},
    ).json()

    shared = client.get(f"/share/{created['token']}")

    assert shared.status_code == 200
    assert shared.headers["content-security-policy"].startswith("default-src 'none'")
    assert "<strong>summary</strong>" in shared.text
    assert "<script" not in shared.text
    assert "<img" not in shared.text
    assert "onerror" not in shared.text
    assert "javascript:" not in shared.text
    assert "&lt;Title&gt;" in shared.text


def test_revoked_share_token_returns_410_without_content(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, subject="user_a")
    _, transcript = _seed_transcript(
        db_session,
        video_id="share222222",
        title="Revoked",
        summary_md="revoked summary",
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )
    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={"target_kind": "transcript_markdown"},
    ).json()

    revoked = client.post(
        f"/api/share-links/{created['id']}/revoke",
        headers=_auth_headers("user_a"),
    )
    denied = client.get(f"/share/{created['token']}")

    assert revoked.status_code == 200
    assert denied.status_code == 410
    assert "transcript body" not in denied.text


def test_expired_share_token_returns_410_without_content(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, subject="user_a")
    _, transcript = _seed_transcript(
        db_session,
        video_id="shareexpired",
        title="Expired",
        summary_md="expired summary",
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )
    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={
            "target_kind": "summary_markdown",
            "expires_at": (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).isoformat(),
        },
    ).json()

    denied = client.get(f"/share/{created['token']}")

    assert denied.status_code == 410
    assert "expired summary" not in denied.text


def test_share_link_rejects_naive_expires_at(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, subject="user_a")
    _, transcript = _seed_transcript(
        db_session,
        video_id="sharenaive",
        title="Naive Expiry",
        summary_md="summary",
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )

    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={
            "target_kind": "summary_markdown",
            "expires_at": "2026-06-01T00:00:00",
        },
    )

    assert created.status_code == 422
    assert db_session.scalars(select(TranscriptShareLink)).all() == []


def test_share_link_owner_isolation(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, subject="user_a")
    _seed_user(db_session, subject="user_b")
    _, transcript = _seed_transcript(
        db_session,
        video_id="share333333",
        title="Owned",
        summary_md="owned summary",
        owner_subject="user_a",
        owner_email="user_a@example.test",
    )
    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
        json={"target_kind": "page"},
    ).json()

    owner_list = client.get(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_a"),
    )
    other_list = client.get(
        f"/api/transcripts/{transcript.id}/share-links",
        headers=_auth_headers("user_b"),
    )
    other_revoke = client.post(
        f"/api/share-links/{created['id']}/revoke",
        headers=_auth_headers("user_b"),
    )

    assert len(owner_list.json()) == 1
    assert other_list.status_code == 404
    assert other_revoke.status_code == 404
