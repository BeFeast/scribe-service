from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from scribe.api import auth as auth_module
from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Owner, Transcript, User, UserRole
from scribe.main import app

EXTERNAL = {"x-forwarded-for": "203.0.113.10"}


@pytest.fixture(autouse=True)
def clean_auth_tables(db_session, monkeypatch):
    monkeypatch.setattr(settings, "clerk_header_secret", "test-header-secret")
    db_session.execute(delete(Job))
    db_session.execute(delete(Owner))
    db_session.commit()
    yield
    db_session.execute(delete(Job))
    db_session.execute(delete(Owner))
    db_session.commit()


@contextmanager
def _client(db_session):
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def _clerk(email: str, subject: str = "user_123") -> dict[str, str]:
    return EXTERNAL | {
        "x-scribe-clerk-secret": "test-header-secret",
        "x-clerk-user-id": subject,
        "x-clerk-user-email": email,
        "x-clerk-user-name": "Test User",
    }


def _seed_user(db_session, *, email: str, role: UserRole = UserRole.user, subject: str = "user_123") -> User:
    owner = Owner(display_name=email)
    db_session.add(owner)
    db_session.flush()
    user = User(
        owner_id=owner.id,
        clerk_subject=subject,
        primary_email=email,
        display_name=email,
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _seed_transcript(db_session, *, owner_id: int | None, video_id: str):
    job = Job(
        url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.done,
        owner_id=owner_id,
    )
    db_session.add(job)
    db_session.flush()
    transcript = Transcript(
        job_id=job.id,
        owner_id=owner_id,
        video_id=video_id,
        title=video_id,
        transcript_md="hello",
        summary_md="world",
    )
    db_session.add(transcript)
    db_session.commit()
    return job, transcript


def test_external_unauthenticated_post_jobs_is_rejected(db_session):
    with _client(db_session) as client:
        resp = client.post("/jobs", json={"url": "https://youtu.be/jNQXAC9IVRw"}, headers=EXTERNAL)
    assert resp.status_code == 401


def test_unsigned_clerk_headers_are_rejected_when_header_secret_is_unset(db_session, monkeypatch):
    monkeypatch.setattr(settings, "clerk_header_secret", "")
    monkeypatch.setattr(settings, "auth_bootstrap_admin_email", "admin@example.com")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw"},
            headers=_clerk("admin@example.com", "clerk_admin"),
        )
    assert resp.status_code == 401


def test_trusted_lan_post_jobs_still_works(db_session):
    with _client(db_session) as client:
        resp = client.post("/jobs", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    assert resp.status_code == 201, resp.text


def test_bootstrap_admin_clerk_user_can_post_jobs(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_bootstrap_admin_email", "admin@example.com")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw"},
            headers=_clerk("admin@example.com", "clerk_admin"),
        )
    assert resp.status_code == 201, resp.text
    user = db_session.query(User).filter_by(primary_email="admin@example.com").one()
    assert user.role == UserRole.admin
    job = db_session.get(Job, resp.json()["job_id"])
    assert job is not None
    assert job.owner_id == user.owner_id


def test_bootstrap_admin_race_loads_existing_user(db_session, monkeypatch):
    monkeypatch.setattr(settings, "auth_bootstrap_admin_email", "admin@example.com")
    original_create = auth_module._create_local_user

    def create_competing_user(session, owner, role):
        from scribe.db.session import SessionLocal

        with SessionLocal() as competing_session:
            original_create(competing_session, owner, role)
            competing_session.commit()
        return original_create(session, owner, role)

    monkeypatch.setattr(auth_module, "_create_local_user", create_competing_user)
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw"},
            headers=_clerk("admin@example.com", "clerk_admin"),
        )
    assert resp.status_code == 201, resp.text
    user = db_session.query(User).filter_by(primary_email="admin@example.com").one()
    assert user.clerk_subject == "clerk_admin"


def test_unauthorized_clerk_user_is_forbidden(db_session):
    _seed_user(db_session, email="allowed@example.com", subject="clerk_allowed")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw"},
            headers=_clerk("other@example.com", "clerk_other"),
        )
    assert resp.status_code == 403


def test_machine_bearer_token_can_submit_outside_lan(db_session, monkeypatch):
    monkeypatch.setattr(settings, "machine_bearer_token", "machine-secret")
    headers = EXTERNAL | {"authorization": "Bearer machine-secret"}
    with _client(db_session) as client:
        resp = client.post("/jobs", json={"url": "https://youtu.be/jNQXAC9IVRw"}, headers=headers)
    assert resp.status_code == 201, resp.text


def test_extension_token_can_submit_outside_lan(db_session):
    user = _seed_user(db_session, email="admin@example.com", role=UserRole.admin, subject="clerk_admin")
    with _client(db_session) as client:
        token_resp = client.post(
            "/api/auth/extension-token",
            json={"label": "Chrome"},
            headers=_clerk(user.primary_email, user.clerk_subject or ""),
        )
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

        submit = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw", "source": "chrome-extension"},
            headers=EXTERNAL | {"authorization": f"Bearer {token}"},
        )
    assert submit.status_code == 201, submit.text
    job = db_session.get(Job, submit.json()["job_id"])
    assert job is not None
    assert job.owner_id == user.owner_id


def test_admin_user_upsert_rejects_duplicate_clerk_subject_on_create(db_session):
    _seed_user(db_session, email="alice@example.com", subject="clerk_alice")
    with _client(db_session) as client:
        resp = client.post(
            "/api/admin/users",
            json={"email": "bob@example.com", "role": "user", "clerk_subject": "clerk_alice"},
        )
    assert resp.status_code == 409


def test_admin_user_upsert_rejects_duplicate_clerk_subject_on_update(db_session):
    _seed_user(db_session, email="alice@example.com", subject="clerk_alice")
    _seed_user(db_session, email="bob@example.com", subject="clerk_bob")
    with _client(db_session) as client:
        resp = client.post(
            "/api/admin/users",
            json={"email": "bob@example.com", "role": "user", "clerk_subject": "clerk_alice"},
        )
    assert resp.status_code == 409


def test_extension_token_cannot_mint_another_extension_token(db_session):
    user = _seed_user(db_session, email="admin@example.com", role=UserRole.admin, subject="clerk_admin")
    with _client(db_session) as client:
        token_resp = client.post(
            "/api/auth/extension-token",
            json={"label": "Chrome"},
            headers=_clerk(user.primary_email, user.clerk_subject or ""),
        )
        assert token_resp.status_code == 201, token_resp.text
        token = token_resp.json()["token"]

        resp = client.post(
            "/api/auth/extension-token",
            json={"label": "Copied"},
            headers=EXTERNAL | {"authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


def test_library_and_queue_are_scoped_to_authenticated_user(db_session):
    alice = _seed_user(db_session, email="alice@example.com", subject="clerk_alice")
    bob = _seed_user(db_session, email="bob@example.com", subject="clerk_bob")
    _seed_transcript(db_session, owner_id=alice.owner_id, video_id="alicevideo1")
    bob_job, _ = _seed_transcript(db_session, owner_id=bob.owner_id, video_id="bobvideo111")
    bob_job.status = JobStatus.queued
    db_session.commit()

    with _client(db_session) as client:
        library = client.get("/api/library", headers=_clerk("alice@example.com", "clerk_alice"))
        queue = client.get("/api/jobs/active", headers=_clerk("alice@example.com", "clerk_alice"))

    assert library.status_code == 200
    assert [row["video_id"] for row in library.json()["rows"]] == ["alicevideo1"]
    assert queue.status_code == 200
    assert queue.json() == {"jobs": []}
