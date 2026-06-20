"""End-to-end bearer/extension token rotation + revocation tests (#354).

Covers:
* POST /api/config/rotate-token rotates the machine bearer token without a
  restart, demoting the old token into a previous-generation grace window.
* The old token is rejected once the grace window elapses.
* A second rotation demotes the prior current token into the grace window.
* Disabling an extension token rejects it on the very next request.

These require a real Postgres (SCRIBE_TEST_DATABASE_URL) because rotation
state lives in app_config and extension tokens live in extension_tokens.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from scribe.api import routes as routes_module
from scribe.api.auth import clear_jwks_cache, new_extension_token, token_hash
from scribe.api.tokens import (
    MACHINE_BEARER_TOKEN_HASH_KEY,
    MACHINE_BEARER_TOKEN_PREV_HASH_KEY,
    MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY,
    bust_machine_bearer_cache,
)
from scribe.config import settings
from scribe.db.models import AppConfig, ExtensionToken, Owner, User
from scribe.main import app

ENV_TOKEN = "env-machine-bearer-token"


def _external_client(token: str | None = None) -> TestClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app, headers=headers, client=("203.0.113.10", 50000))


def _override_session(db_session):
    app.dependency_overrides[routes_module.get_session] = lambda: db_session


def _restore_session() -> None:
    app.dependency_overrides.pop(routes_module.get_session, None)


def _clear_token_rows(session) -> None:
    session.execute(
        delete(AppConfig).where(
            AppConfig.key.in_(
                [
                    MACHINE_BEARER_TOKEN_HASH_KEY,
                    MACHINE_BEARER_TOKEN_PREV_HASH_KEY,
                    MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY,
                ]
            )
        )
    )
    session.commit()
    bust_machine_bearer_cache()


@pytest.fixture()
def rotate_env(db_session, monkeypatch):
    """External-IP operator env: only the machine bearer token authenticates."""
    _clear_token_rows(db_session)
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", ENV_TOKEN)
    monkeypatch.setattr(settings, "machine_bearer_grace_seconds", 300)
    _override_session(db_session)
    try:
        yield
    finally:
        _restore_session()
        _clear_token_rows(db_session)
        clear_jwks_cache()


def _me(client) -> tuple[int, dict]:
    resp = client.get("/api/auth/me")
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return resp.status_code, body


def test_rotate_returns_new_token_and_old_still_works_in_grace(rotate_env, db_session):
    # Pre-rotation: env token authenticates.
    status, body = _me(_external_client(ENV_TOKEN))
    assert status == 200
    assert body["kind"] == "machine"

    rotate = _external_client(ENV_TOKEN).post("/api/config/rotate-token")
    assert rotate.status_code == 200
    new_token = rotate.json()["token"]
    assert new_token.startswith("stb_")
    assert new_token != ENV_TOKEN
    assert rotate.json()["grace_seconds"] == 300

    # New token authenticates immediately.
    status, body = _me(_external_client(new_token))
    assert status == 200, body
    assert body["kind"] == "machine"

    # Old env token still accepted inside the grace window.
    status, _ = _me(_external_client(ENV_TOKEN))
    assert status == 200

    # A random token is rejected.
    status, _ = _me(_external_client("stb_not-the-real-token"))
    assert status == 401

    # The hash of the new token is persisted; the plaintext is not.
    rows = dict(db_session.execute(
        select(AppConfig.key, AppConfig.value)
    ).all())
    assert ENV_TOKEN not in rows.get(MACHINE_BEARER_TOKEN_HASH_KEY, "")
    assert new_token not in rows.get(MACHINE_BEARER_TOKEN_HASH_KEY, "")
    assert hashlib.sha256(new_token.encode()).hexdigest() == rows[MACHINE_BEARER_TOKEN_HASH_KEY]


def test_old_token_rejected_after_grace_window(rotate_env, db_session):
    rotate = _external_client(ENV_TOKEN).post("/api/config/rotate-token")
    new_token = rotate.json()["token"]

    # Inside grace: old token still works.
    assert _me(_external_client(ENV_TOKEN))[0] == 200

    # Backdate the rotation past the grace window.
    row = db_session.get(AppConfig, MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY)
    assert row is not None
    row.value = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=600)).isoformat()
    db_session.commit()
    bust_machine_bearer_cache()

    # After grace: old token rejected, new token still works.
    assert _me(_external_client(ENV_TOKEN))[0] == 401
    assert _me(_external_client(new_token))[0] == 200


def test_zero_grace_rejects_old_token_immediately(rotate_env, db_session, monkeypatch):
    monkeypatch.setattr(settings, "machine_bearer_grace_seconds", 0)
    bust_machine_bearer_cache()
    rotate = _external_client(ENV_TOKEN).post("/api/config/rotate-token")
    new_token = rotate.json()["token"]

    # grace=0: the previous generation is rejected on the very next request.
    assert _me(_external_client(ENV_TOKEN))[0] == 401
    assert _me(_external_client(new_token))[0] == 200


def test_second_rotation_demotes_current_to_previous(rotate_env, db_session):
    first = _external_client(ENV_TOKEN).post("/api/config/rotate-token").json()["token"]
    second = _external_client(first).post("/api/config/rotate-token").json()["token"]

    # Second token is active; first token is demoted into the grace window.
    assert _me(_external_client(second))[0] == 200
    assert _me(_external_client(first))[0] == 200

    # Backdate past grace: first token rejected, second still works, env also
    # rejected (it was demoted out two rotations ago and is no longer prev).
    row = db_session.get(AppConfig, MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY)
    assert row is not None
    row.value = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=600)).isoformat()
    db_session.commit()
    bust_machine_bearer_cache()

    assert _me(_external_client(first))[0] == 401
    assert _me(_external_client(second))[0] == 200
    assert _me(_external_client(ENV_TOKEN))[0] == 401


def test_disabled_extension_token_rejected_immediately(db_session, monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", "")
    _override_session(db_session)
    try:
        owner = Owner(display_name="Extension User")
        user = User(
            owner=owner,
            clerk_subject="ext-subject-1",
            primary_email="ext@example.test",
            display_name="Extension User",
            role="user",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        token = new_extension_token()
        row = ExtensionToken(
            user_id=user.id,
            token_hash=token_hash(token),
            label="Chrome extension",
        )
        db_session.add(row)
        db_session.commit()

        client = _external_client(token)
        assert _me(client)[0] == 200

        # Disable and commit; the very next request must be rejected.
        row.disabled = True
        db_session.commit()
        # No cache to bust — extension-token disabled state is read fresh per
        # request, which is the whole point of the immediate-revocation gate.
        assert _me(client)[0] == 401
    finally:
        _restore_session()
        db_session.rollback()
