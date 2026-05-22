from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import ExtensionToken, Job, JobStatus, Owner, Transcript, User
from scribe.main import app


def _client(db_session):
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    return TestClient(app)


def _external_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"X-Forwarded-For": "203.0.113.10"}
    if extra:
        headers.update(extra)
    return headers


def _clear_auth(session) -> None:
    session.execute(delete(ExtensionToken))
    session.execute(delete(User))
    session.execute(delete(Owner))
    session.commit()


def _seed_user(session, *, email: str, subject: str, role: str = "user", disabled: bool = False) -> User:
    owner = Owner(display_name=email)
    user = User(
        owner=owner,
        clerk_subject=subject,
        primary_email=email,
        display_name=email,
        role=role,
        disabled=disabled,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_transcript(session, *, video_id: str, owner_id: int | None) -> Transcript:
    job = Job(
        url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.done,
        owner_id=owner_id,
    )
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id,
        video_id=video_id,
        title=video_id,
        transcript_md="transcript",
        summary_md="summary",
        owner_id=owner_id,
    )
    session.add(transcript)
    session.commit()
    return transcript


def test_external_unauthenticated_post_jobs_is_rejected(db_session):
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/jNQXAC9IVRw"},
            headers=_external_headers(),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 401


def test_trusted_lan_post_jobs_still_works(db_session):
    with _client(db_session) as client:
        resp = client.post("/jobs", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 201, resp.text
    job = db_session.scalar(select(Job).where(Job.video_id == "jNQXAC9IVRw").order_by(Job.id.desc()))
    assert job is not None
    assert job.owner_id is None


def test_authorized_clerk_user_can_post_jobs_and_owns_row(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    user = _seed_user(db_session, email="admin@example.test", subject="user_123", role="admin")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_123",
                    "X-Scribe-Test-Email": "admin@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 201, resp.text
    job = db_session.get(Job, resp.json()["job_id"])
    assert job is not None
    assert job.owner_id == user.owner_id


def test_bootstrap_admin_email_creates_first_user(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    monkeypatch.setattr(settings, "bootstrap_admin_email", "first@example.test")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_first",
                    "X-Scribe-Test-Email": "first@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 201, resp.text
    user = db_session.scalar(select(User).where(User.primary_email == "first@example.test"))
    assert user is not None
    assert user.role == "admin"
    assert user.clerk_subject == "user_first"


def test_unauthorized_clerk_user_is_rejected(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, email="allowed@example.test", subject="user_allowed")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_blocked",
                    "X-Scribe-Test-Email": "blocked@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 403


def test_extension_token_can_submit_outside_lan(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    user = _seed_user(db_session, email="ext@example.test", subject="user_ext")
    with _client(db_session) as client:
        token_resp = client.post(
            "/api/auth/extension-token",
            json={"label": "Chrome"},
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_ext",
                    "X-Scribe-Test-Email": "ext@example.test",
                }
            ),
        )
        token = token_resp.json()["token"]
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/9bZkp7q19f0"},
            headers=_external_headers({"Authorization": f"Bearer {token}"}),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 201, resp.text
    job = db_session.get(Job, resp.json()["job_id"])
    assert job is not None
    assert job.owner_id == user.owner_id


def test_machine_bearer_token_can_submit_outside_lan(db_session, monkeypatch):
    monkeypatch.setattr(settings, "machine_bearer_token", "machine-test-token")
    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/3JZ_D3ELwOQ"},
            headers=_external_headers({"Authorization": "Bearer machine-test-token"}),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)
    assert resp.status_code == 201, resp.text


def test_library_is_owner_scoped_for_users_and_broad_for_admin(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    first = _seed_user(db_session, email="one@example.test", subject="user_one")
    second = _seed_user(db_session, email="two@example.test", subject="user_two")
    admin = _seed_user(db_session, email="admin2@example.test", subject="user_admin2", role="admin")
    first_transcript = _seed_transcript(db_session, video_id="ownerone111", owner_id=first.owner_id)
    second_transcript = _seed_transcript(db_session, video_id="ownertwo222", owner_id=second.owner_id)

    with _client(db_session) as client:
        user_resp = client.get(
            "/api/library",
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_one",
                    "X-Scribe-Test-Email": "one@example.test",
                }
            ),
        )
        admin_resp = client.get(
            "/api/library",
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_admin2",
                    "X-Scribe-Test-Email": "admin2@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert admin.owner_id is not None
    user_ids = {row["id"] for row in user_resp.json()["rows"]}
    assert user_ids == {first_transcript.id}
    admin_ids = {row["id"] for row in admin_resp.json()["rows"]}
    assert {first_transcript.id, second_transcript.id}.issubset(admin_ids)
