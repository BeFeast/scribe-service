from __future__ import annotations

import logging

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from scribe.api import auth as auth_module
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


def _seed_user(session, *, email: str, subject: str | None, role: str = "user", disabled: bool = False) -> User:
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


def _seed_transcript(
    session, *, video_id: str, owner_id: int | None, owner_subject: str | None = None
) -> Transcript:
    job = Job(
        url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.done,
        owner_id=owner_id,
        owner_subject=owner_subject,
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
        owner_subject=owner_subject,
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


def test_owner_scoped_reads_include_pre_migration_subject_rows(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, email="legacy@example.test", subject="user_legacy")
    transcript = _seed_transcript(
        db_session,
        video_id="legacy11111",
        owner_id=None,
        owner_subject="user_legacy",
    )

    with _client(db_session) as client:
        resp = client.get(
            "/api/library",
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_legacy",
                    "X-Scribe-Test-Email": "legacy@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert resp.status_code == 200, resp.text
    assert {row["id"] for row in resp.json()["rows"]} == {transcript.id}


def test_clerk_identity_conflict_returns_forbidden(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, email="subject@example.test", subject="user_subject")
    _seed_user(db_session, email="email@example.test", subject="user_email")

    with _client(db_session) as client:
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
            headers=_external_headers(
                {
                    "X-Scribe-Test-Clerk-Sub": "user_subject",
                    "X-Scribe-Test-Email": "email@example.test",
                }
            ),
        )
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert resp.status_code == 403
    assert "conflicts" in resp.json()["detail"]


def test_auth_test_mode_warns_on_startup(db_session, monkeypatch, caplog):
    monkeypatch.setattr(settings, "auth_test_mode", True)
    caplog.set_level(logging.WARNING, logger="scribe")
    with _client(db_session):
        pass
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert "auth test mode is enabled" in caplog.text


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


def test_clerk_token_without_email_links_existing_user_from_backend_profile(db_session, monkeypatch):
    _clear_auth(db_session)
    user = _seed_user(db_session, email="owner@example.test", subject=None, role="admin")
    auth_module.clear_jwks_cache()
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_profile")
    monkeypatch.setattr(settings, "clerk_backend_api_url", "https://api.clerk.test")
    monkeypatch.setattr(auth_module, "_validate_clerk_user", lambda _token: {"sub": "user_profile"})

    class ProfileResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "id": "user_profile",
                "first_name": "Owner",
                "last_name": "Example",
                "primary_email_address_id": "email_primary",
                "email_addresses": [
                    {"id": "email_primary", "email_address": "owner@example.test"},
                ],
            }

    def fake_get(url, *, headers, timeout):
        assert url == "https://api.clerk.test/v1/users/user_profile"
        assert headers == {"Authorization": "Bearer sk_test_profile"}
        assert timeout == 5.0
        return ProfileResponse()

    monkeypatch.setattr(auth_module.httpx, "get", fake_get)

    actor = auth_module._actor_from_clerk_token(db_session, "token-without-email")

    db_session.refresh(user)
    assert actor.user_id == user.id
    assert actor.role == "admin"
    assert actor.email == "owner@example.test"
    assert user.clerk_subject == "user_profile"
    assert user.display_name == "Owner Example"


def test_clerk_token_without_email_uses_existing_subject_mapping(db_session, monkeypatch):
    _clear_auth(db_session)
    user = _seed_user(db_session, email="mapped@example.test", subject="user_mapped")
    auth_module.clear_jwks_cache()
    monkeypatch.setattr(settings, "clerk_secret_key", "")
    monkeypatch.setattr(auth_module, "_validate_clerk_user", lambda _token: {"sub": "user_mapped"})

    def fail_get(*_args, **_kwargs):
        raise AssertionError("existing subject mapping must not call Clerk backend")

    monkeypatch.setattr(auth_module.httpx, "get", fail_get)

    actor = auth_module._actor_from_clerk_token(db_session, "token-without-email")

    assert actor.user_id == user.id
    assert actor.email == "mapped@example.test"


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


def test_admin_user_apis_require_admin_role(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    _seed_user(db_session, email="user@example.test", subject="user_regular", role="user")

    headers = _external_headers(
        {
            "X-Scribe-Test-Clerk-Sub": "user_regular",
            "X-Scribe-Test-Email": "user@example.test",
        }
    )
    with _client(db_session) as client:
        list_resp = client.get("/api/admin/users", headers=headers)
        add_resp = client.post(
            "/api/admin/users",
            json={"email": "new@example.test", "role": "user"},
            headers=headers,
        )
        disable_resp = client.post("/api/admin/users/1/disable", headers=headers)
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert list_resp.status_code == 403
    assert add_resp.status_code == 403
    assert disable_resp.status_code == 403
    assert list_resp.json()["detail"] == "admin role required"


def test_admin_user_apis_list_add_update_and_disable(db_session, monkeypatch):
    _clear_auth(db_session)
    monkeypatch.setattr(settings, "auth_test_mode", True)
    admin = _seed_user(db_session, email="admin@example.test", subject="user_admin", role="admin")
    target = _seed_user(db_session, email="target@example.test", subject="user_target", role="user")

    headers = _external_headers(
        {
            "X-Scribe-Test-Clerk-Sub": "user_admin",
            "X-Scribe-Test-Email": "admin@example.test",
        }
    )
    with _client(db_session) as client:
        list_resp = client.get("/api/admin/users", headers=headers)
        create_resp = client.post(
            "/api/admin/users",
            json={"email": "new@example.test", "display_name": "New User", "role": "user"},
            headers=headers,
        )
        update_resp = client.post(
            "/api/admin/users",
            json={"email": "target@example.test", "display_name": "Target Admin", "role": "admin"},
            headers=headers,
        )
        disable_resp = client.post(f"/api/admin/users/{target.id}/disable", headers=headers)
    app.dependency_overrides.pop(routes_module.get_session, None)

    assert list_resp.status_code == 200, list_resp.text
    listed = {row["primary_email"]: row for row in list_resp.json()}
    assert listed["admin@example.test"]["role"] == "admin"
    assert create_resp.status_code == 201, create_resp.text
    assert create_resp.json()["primary_email"] == "new@example.test"
    assert create_resp.json()["display_name"] == "New User"
    assert update_resp.status_code == 201, update_resp.text
    assert update_resp.json()["role"] == "admin"
    assert update_resp.json()["display_name"] == "Target Admin"
    assert disable_resp.status_code == 200, disable_resp.text
    assert disable_resp.json()["disabled"] is True
    assert admin.id != target.id


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
