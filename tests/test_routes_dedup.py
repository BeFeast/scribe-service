"""DB-coupled tests for POST /jobs dedup + GET /jobs/<id>.

These require SCRIBE_TEST_DATABASE_URL to point at a real Postgres (scribe
uses ARRAY[Text] which SQLite cannot represent). Skipped by default; CI
provides a postgres service container."""
from __future__ import annotations

import base64
import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.main import app
from scribe.pipeline import summarizer as summarizer_module


@pytest.fixture()
def client(db_session):
    """TestClient that uses our test session for every request via dependency
    override. The session is rolled back per-test (`db_session` fixture)."""
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)


def _unsigned_jwt(claims: dict[str, object]) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def _part(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_part(header)}.{_part(claims)}."


def _seed_done_transcript(
    session,
    *,
    video_id: str,
    title: str = "test",
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
        job_id=job.id, video_id=video_id, title=title,
        transcript_md="hello", summary_md="world", tags=["tag1"],
        owner_subject=owner_subject,
        owner_email=owner_email,
    )
    session.add(transcript)
    session.commit()
    return job, transcript


def _seed_partial_transcript(session, *, video_id: str):
    """Whisper-done, summary still NULL — the new partial state from P0.1."""
    job = Job(url=f"https://youtu.be/{video_id}", video_id=video_id, status=JobStatus.failed,
              error="codex died")
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id, video_id=video_id, title="partial",
        transcript_md="hello", summary_md=None, tags=None,
    )
    session.add(transcript)
    session.commit()
    return job, transcript


def test_post_jobs_dedup_returns_done_transcript(client, db_session):
    _, transcript = _seed_done_transcript(db_session, video_id="jNQXAC9IVRw")
    resp = client.post("/jobs", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduplicated"] is True
    assert body["status"] == "done"
    assert body["transcript"]["id"] == transcript.id


VALID_COOKIES_BLOB = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\topaque-value\n"
)


def test_post_jobs_with_cookies_stashes_blob_in_jar(client, db_session, monkeypatch):
    """The validated cookie blob must land in the in-process cookie jar
    keyed by the new job_id and must NOT appear on the Job row or in the
    response body. The worker pops it on the download stage (#313)."""
    from scribe.api import cookie_jar
    from scribe.db.models import Owner, User

    monkeypatch.setattr(settings, "auth_test_mode", True)
    owner = Owner(display_name="owner@example.test")
    user = User(
        owner=owner,
        clerk_subject="user_with_cookies",
        primary_email="owner@example.test",
        display_name="owner@example.test",
        role="user",
    )
    db_session.add(user)
    db_session.commit()

    resp = client.post(
        "/jobs",
        json={
            "url": "https://youtu.be/cookiejob42",
            "youtube_cookies": VALID_COOKIES_BLOB,
        },
        headers={
            "X-Scribe-Test-Clerk-Sub": "user_with_cookies",
            "X-Scribe-Test-Email": "owner@example.test",
        },
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job_id"]
    try:
        assert "LOGIN_INFO" not in resp.text
        assert "opaque-value" not in resp.text
        # The blob was stashed for the worker (popped exactly once).
        assert cookie_jar.take(job_id) == VALID_COOKIES_BLOB
        # No DB column carries the blob — Job has no youtube_cookies attr.
        job = db_session.get(Job, job_id)
        assert job is not None
        assert not hasattr(job, "youtube_cookies")
    finally:
        cookie_jar.discard(job_id)


def test_post_jobs_without_cookies_leaves_jar_untouched(client, db_session):
    from scribe.api import cookie_jar

    resp = client.post("/jobs", json={"url": "https://youtu.be/nocookies1"})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job_id"]
    assert cookie_jar.take(job_id) is None


def test_post_jobs_accepts_x_url_into_queue(client, db_session):
    resp = client.post("/jobs", json={"url": "https://x.com/i/status/2057105488165163198"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduplicated"] is False
    assert body["status"] == "queued"
    assert body["video_id"].startswith("pending:")

    job = db_session.get(Job, body["job_id"])
    assert job is not None
    assert job.url == "https://x.com/i/status/2057105488165163198"
    assert job.video_id == body["video_id"]


def test_post_jobs_dedup_partial_links_existing_and_enqueues_retry(client, db_session):
    """Re-submitting a video with a partial transcript (whisper done, summary
    failed) must dedup to the existing Transcript and enqueue a summary-only
    retry: no duplicate Transcript row, and the new Job is queued (the worker's
    resume path then skips download+whisper). Regression for #265: previously a
    partial transcript silently re-ran the full whisper transcription on Vast."""
    old_job, partial = _seed_partial_transcript(db_session, video_id="partial1234")
    transcripts_before = db_session.scalar(select(func.count()).select_from(Transcript))
    jobs_before = db_session.scalar(select(func.count()).select_from(Job))

    resp = client.post("/jobs", json={"url": "https://youtu.be/partial1234"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deduplicated"] is True
    assert body["status"] == "queued"
    assert body["transcript"]["id"] == partial.id
    # New retry Job is queued (the worker resume path will summarize-only) and
    # is distinct from the failed owning job — but no new Transcript row exists.
    assert body["job_id"] != old_job.id
    assert db_session.scalar(select(func.count()).select_from(Transcript)) == transcripts_before
    assert db_session.scalar(select(func.count()).select_from(Job)) == jobs_before + 1
    retry = db_session.get(Job, body["job_id"])
    assert retry is not None
    assert retry.status == JobStatus.queued
    assert retry.video_id == partial.video_id


def test_post_jobs_clerk_owner_is_stored(client, db_session):
    token = _unsigned_jwt({"sub": "user_clerk_123", "email": "clerk@example.test", "name": "Clerk User"})

    resp = client.post(
        "/jobs",
        json={"url": "https://youtu.be/clerkown123"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201, resp.text
    job = db_session.get(Job, resp.json()["job_id"])
    assert job.owner_subject == "user_clerk_123"
    assert job.owner_email == "clerk@example.test"
    assert job.owner_display_name == "Clerk User"


def test_post_jobs_trusted_lan_uses_default_owner(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "default_owner_subject", "default-subject")
    monkeypatch.setattr(settings, "default_owner_email", "default@example.test")
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")

    resp = client.post(
        "/jobs",
        json={"url": "https://youtu.be/lanowner12"},
        headers={"X-Forwarded-For": "10.10.0.42"},
    )

    assert resp.status_code == 201, resp.text
    job = db_session.get(Job, resp.json()["job_id"])
    assert job.owner_subject == "default-subject"
    assert job.owner_email == "default@example.test"


def test_post_jobs_machine_bearer_uses_default_owner(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "default_owner_subject", "default-subject")
    monkeypatch.setattr(settings, "default_owner_email", "default@example.test")
    monkeypatch.setattr(settings, "machine_bearer_token", "machine-token")

    resp = client.post(
        "/jobs",
        json={"url": "https://youtu.be/machown123"},
        headers={"Authorization": "Bearer machine-token"},
    )

    assert resp.status_code == 201, resp.text
    job = db_session.get(Job, resp.json()["job_id"])
    assert job.owner_subject == "default-subject"
    assert job.owner_email == "default@example.test"


def test_post_jobs_dedup_scoped_to_current_owner(client, db_session):
    _seed_done_transcript(
        db_session,
        video_id="ownerdedup1",
        owner_subject="other-owner",
        owner_email="other@example.test",
    )
    token = _unsigned_jwt({"sub": "current-owner", "email": "current@example.test"})

    resp = client.post(
        "/jobs",
        json={"url": "https://youtu.be/ownerdedup1"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["deduplicated"] is False
    job = db_session.get(Job, resp.json()["job_id"])
    assert job.owner_subject == "current-owner"


def test_get_job_returns_transcript_by_video_id(client, db_session):
    """GET /jobs/<id> looks up the transcript by video_id (not job_id) so a
    resumed run that re-parented the transcript still surfaces it."""
    old_job, transcript = _seed_done_transcript(db_session, video_id="resumed1234")
    new_job = Job(
        url="https://youtu.be/resumed1234", video_id="resumed1234", status=JobStatus.done
    )
    db_session.add(new_job)
    db_session.commit()
    # transcript still points at old_job; the GET should still find it via video_id
    resp = client.get(f"/jobs/{new_job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"]["id"] == transcript.id


def test_get_job_uses_provider_aware_source_label_for_x(client, db_session):
    job, transcript = _seed_done_transcript(
        db_session,
        video_id="xstatus123",
        url="https://x.com/example/status/123",
    )

    resp = client.get(f"/jobs/{job.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_label"] == "Twitter/X"
    assert body["source_url"] == "https://x.com/example/status/123"
    assert body["transcript"]["source_label"] == "Twitter/X"
    assert body["transcript"]["source_url"] == "https://x.com/example/status/123"
    assert body["transcript"]["id"] == transcript.id


def test_get_summary_md_409_on_partial(client, db_session):
    _, transcript = _seed_partial_transcript(db_session, video_id="partial2345")
    resp = client.get(f"/transcripts/{transcript.id}/summary.md")
    assert resp.status_code == 409
    assert "partial" in resp.text.lower()


def test_admin_retry_creates_new_job_and_links_failed(client, db_session):
    """Failed → terminal → retry queues a fresh Job carrying same url+source,
    and the original job's error is annotated with the new job id."""
    old_job, _ = _seed_partial_transcript(db_session, video_id="retryfail123")
    old_job.source = "telegram"
    db_session.commit()
    original_error = old_job.error

    resp = client.post(f"/admin/jobs/{old_job.id}/retry")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    new_job_id = body["job_id"]
    assert new_job_id != old_job.id
    assert body["status"] == "queued"
    assert body["url"] == old_job.url
    assert body["video_id"] == old_job.video_id

    new_job = db_session.get(Job, new_job_id)
    assert new_job is not None
    assert new_job.source == "telegram"
    assert new_job.video_id == old_job.video_id

    db_session.refresh(old_job)
    assert old_job.error is not None
    assert f"recovered as job {new_job_id}" in old_job.error
    # original error message is preserved as a prefix
    assert old_job.error.startswith(original_error)


def test_admin_retry_creates_new_job_for_done(client, db_session):
    """Done jobs are terminal too — operator can force a fresh run even though
    POST /jobs would otherwise dedup against the existing transcript. The
    original `error` must stay NULL so clients reading `error != null` as a
    failure indicator don't mis-flag a clean terminal job."""
    old_job, _ = _seed_done_transcript(db_session, video_id="retrydone123")
    assert old_job.error is None

    resp = client.post(f"/admin/jobs/{old_job.id}/retry")
    assert resp.status_code == 201, resp.text
    new_job_id = resp.json()["job_id"]
    assert new_job_id != old_job.id

    db_session.refresh(old_job)
    assert old_job.status == JobStatus.done
    assert old_job.error is None


def test_admin_retry_rejects_active_for_same_video(client, db_session):
    """A second retry attempt while the first recovery is still queued/in-flight
    must 409 — otherwise operators can silently fork parallel pipelines and
    double the Vast spend on the same video."""
    old_job, _ = _seed_partial_transcript(db_session, video_id="retryactive1")

    first = client.post(f"/admin/jobs/{old_job.id}/retry")
    assert first.status_code == 201, first.text
    first_new_id = first.json()["job_id"]

    second = client.post(f"/admin/jobs/{old_job.id}/retry")
    assert second.status_code == 409
    detail = second.json()["detail"].lower()
    assert "active" in detail
    assert str(first_new_id) in detail


def test_admin_retry_rejects_non_terminal(client, db_session):
    """Queued/in-flight jobs must not be forked — return 409 and keep state."""
    active = Job(
        url="https://youtu.be/active123abc", video_id="active123abc",
        status=JobStatus.transcribing,
    )
    db_session.add(active)
    db_session.commit()

    resp = client.post(f"/admin/jobs/{active.id}/retry")
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "transcribing" in detail
    assert "non-terminal" in detail

    db_session.refresh(active)
    assert active.status == JobStatus.transcribing
    assert active.error is None


def test_admin_retry_returns_404_for_missing_job(client):
    resp = client.post("/admin/jobs/999999/retry")
    assert resp.status_code == 404


def test_list_transcripts_hides_partials_by_default(client, db_session):
    _, done = _seed_done_transcript(db_session, video_id="doneabc1234", title="done")
    _, partial = _seed_partial_transcript(db_session, video_id="partbcd2345")
    default = client.get("/transcripts").json()
    ids_default = {row["id"] for row in default}
    assert done.id in ids_default
    assert partial.id not in ids_default

    with_partial = client.get("/transcripts", params={"include_partial": True}).json()
    ids_partial = {row["id"] for row in with_partial}
    assert done.id in ids_partial
    assert partial.id in ids_partial


# ---------------------------------------------------------------- resummarize
def _stub_summarizer(monkeypatch, *, summary_md: str = "regenerated summary",
                     tags: list[str] | None = None) -> None:
    """Replace `summarizer.summarize` so /resummarize never shells out to codex.
    The route imports `from scribe.pipeline import summarizer` and calls
    `summarizer.summarize`, so patching the module attribute is sufficient."""
    def _fake(_transcript_md, *, title, lock_timeout=None, **__):
        return summarizer_module.SummaryResult(summary_md=summary_md, tags=tags or [])
    monkeypatch.setattr(summarizer_module, "summarize", _fake)


def test_resummarize_html_redirects_with_flash_cookie(client, db_session, monkeypatch):
    """Web UI button path: a browser POST (Accept: text/html) gets a 303 to the
    detail page with a one-shot flash cookie. Backs PRD §4.9 acceptance:
    'redirect + flash message'."""
    _stub_summarizer(monkeypatch, summary_md="fresh summary", tags=["topic-a"])
    _, transcript = _seed_partial_transcript(db_session, video_id="resumhtmll12")
    csrf = "csrf-token"
    client.cookies.set(routes_module.CSRF_COOKIE, csrf)
    resp = client.post(
        f"/transcripts/{transcript.id}/resummarize",
        data={"csrf_token": csrf},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == f"/transcripts/{transcript.id}"
    flash = resp.cookies.get(routes_module.FLASH_COOKIE)
    assert flash is not None
    level, _, encoded = flash.partition("|")
    assert level == "success"
    assert "regenerated" in unquote(encoded).lower()

    db_session.refresh(transcript)
    assert transcript.summary_md == "fresh summary"
    assert transcript.tags == ["topic-a"]


def test_resummarize_html_rejects_missing_csrf(client, db_session, monkeypatch):
    _stub_summarizer(monkeypatch)
    _, transcript = _seed_partial_transcript(db_session, video_id="resumcsrf12")
    resp = client.post(
        f"/transcripts/{transcript.id}/resummarize",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_resummarize_json_still_returns_brief(client, db_session, monkeypatch):
    """API clients (Accept: application/json) keep the existing TranscriptBrief
    behavior — the web flow's redirect must not break automation."""
    _stub_summarizer(monkeypatch, summary_md="api summary")
    _, transcript = _seed_partial_transcript(db_session, video_id="resumjsonl12")
    resp = client.post(
        f"/transcripts/{transcript.id}/resummarize",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == transcript.id
    assert routes_module.FLASH_COOKIE not in resp.cookies


def test_transcript_detail_html_redirects_to_spa(client, db_session):
    _, transcript = _seed_done_transcript(db_session, video_id="detailbtn12")
    resp = client.get(
        f"/transcripts/{transcript.id}",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == f"/#/transcript/{transcript.id}"


def test_transcript_detail_default_returns_json(client, db_session):
    _, transcript = _seed_done_transcript(db_session, video_id="flashlevel12")
    resp = client.get(f"/transcripts/{transcript.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == transcript.id
    assert body["job_id"] == transcript.job_id
    assert body["summary_md"] == "world"
    assert body["transcript_md"] == "hello"


def test_transcript_detail_json_uses_provider_aware_source_label_for_x(client, db_session):
    _, transcript = _seed_done_transcript(
        db_session,
        video_id="xdetail123",
        url="https://twitter.com/example/status/123",
    )

    resp = client.get(f"/transcripts/{transcript.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_label"] == "Twitter/X"
    assert body["source_url"] == "https://twitter.com/example/status/123"


def test_transcript_detail_json_keeps_youtube_source_link(client, db_session):
    _, transcript = _seed_done_transcript(
        db_session,
        video_id="ytlinked12x",
        url="https://www.youtube.com/watch?v=ytlinked12x",
    )

    resp = client.get(f"/transcripts/{transcript.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_label"] == "YouTube"
    assert body["source_url"] == "https://www.youtube.com/watch?v=ytlinked12x"
