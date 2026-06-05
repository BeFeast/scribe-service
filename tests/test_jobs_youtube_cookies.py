"""POST /jobs youtube_cookies acceptance, auth, validation, and log-safety.

These tests stay DB-free: every failure path (auth gate, size cap, format
check) is reached before the route touches the session. The owner-actor
path is exercised via dependency overrides so we don't need Clerk or the
extension-token tables.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.api.auth import Actor
from scribe.api.schemas import (
    YOUTUBE_COOKIES_MAX_BYTES,
    CookieValidationError,
    validate_youtube_cookies,
)
from scribe.main import app

VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\topaque-value\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tVISITOR_INFO1_LIVE\tabc123\n"
)


def _no_db_session():
    class _Forbidden:
        def __getattr__(self, name):
            raise RuntimeError(f"db_session.{name} touched in pure test")
    yield _Forbidden()


def _owner_actor() -> Actor:
    return Actor(
        kind="extension",
        role="user",
        subject="user_x",
        user_id=42,
        owner_id=7,
        email="owner@example.com",
        display_name="Owner",
    )


def _lan_actor() -> Actor:
    # Trusted-LAN: authenticated but not tied to a human owner.
    return Actor(kind="trusted-lan", role="lan")


@pytest.fixture()
def client():
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        app.dependency_overrides.pop(routes_module.require_actor, None)


def _override_actor(actor: Actor) -> None:
    app.dependency_overrides[routes_module.require_actor] = lambda: actor


# -- pure validator --------------------------------------------------------

def test_validator_accepts_real_netscape_blob():
    validate_youtube_cookies(VALID_COOKIES)


def test_validator_rejects_malformed_line():
    with pytest.raises(CookieValidationError) as exc:
        validate_youtube_cookies("youtube.com only-three\tfields\there\n")
    # Message must not contain any portion of the input.
    assert "youtube" not in str(exc.value).lower()
    assert "only-three" not in str(exc.value)


def test_validator_rejects_oversized_blob():
    blob = "#" + ("x" * (YOUTUBE_COOKIES_MAX_BYTES + 1))
    with pytest.raises(CookieValidationError) as exc:
        validate_youtube_cookies(blob)
    assert "byte limit" in str(exc.value)
    assert "xxx" not in str(exc.value)


def test_validator_rejects_empty_blob():
    with pytest.raises(CookieValidationError):
        validate_youtube_cookies("# only a comment\n\n")


# -- route auth gate -------------------------------------------------------

def test_post_jobs_cookies_from_non_owner_actor_is_403(client):
    _override_actor(_lan_actor())
    resp = client.post(
        "/jobs",
        json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "youtube_cookies": VALID_COOKIES,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "owner" in body["detail"].lower()
    # Value never appears in the rejection response.
    assert "LOGIN_INFO" not in resp.text
    assert "opaque-value" not in resp.text


def test_post_jobs_no_cookies_from_non_owner_actor_proceeds(client, monkeypatch):
    """The auth gate must only fire when cookies are present — adding the
    optional field shouldn't change behavior for existing callers."""
    _override_actor(_lan_actor())
    # The route will try to touch the DB after passing the cookie gate;
    # we want to confirm we got past the 403, not actually run the query.
    sentinel = object()

    def _explode(*_a, **_k):
        raise RuntimeError("reached DB path")

    monkeypatch.setattr(routes_module, "initial_video_key", lambda url: sentinel and "vid")
    monkeypatch.setattr(routes_module, "current_owner", _explode)
    resp = client.post(
        "/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
    )
    # We expect a 500 from the _explode hook, NOT a 403.
    assert resp.status_code == 500


# -- route validation ------------------------------------------------------

def test_post_jobs_oversized_cookies_is_422_and_does_not_log_value(client, caplog):
    _override_actor(_owner_actor())
    secret = "SECRET_COOKIE_MARKER_xyz123"
    blob = (
        "# Netscape HTTP Cookie File\n"
        + ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\t"
        + secret
        + "\n"
        + ("# pad " + "p" * 64 + "\n") * ((YOUTUBE_COOKIES_MAX_BYTES // 70) + 10)
    )
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "youtube_cookies": blob},
        )
    assert resp.status_code == 422
    assert "byte limit" in resp.json()["detail"]
    assert secret not in resp.text
    # Sanity: secret should not have been emitted to logs either.
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_post_jobs_malformed_cookies_is_422_and_does_not_log_value(client, caplog):
    _override_actor(_owner_actor())
    secret = "another_SECRET_marker_98765"
    blob = "youtube.com one-field-only " + secret + "\n"
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "youtube_cookies": blob},
        )
    assert resp.status_code == 422
    assert "Netscape" in resp.json()["detail"]
    assert secret not in resp.text
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_post_jobs_invalid_cookies_422_response_omits_input(client):
    """RequestValidationError would normally echo `input` back to the
    caller, leaking the cookie blob. We validate inside the route + raise
    HTTPException ourselves, so the value must never appear in the body."""
    _override_actor(_owner_actor())
    secret = "do-not-leak-this-cookie-value"
    blob = "youtube.com\t" + secret + "\n"  # only 2 tab fields
    resp = client.post(
        "/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "youtube_cookies": blob},
    )
    assert resp.status_code == 422
    assert secret not in resp.text


# -- field is optional -----------------------------------------------------

def test_post_jobs_without_field_keeps_existing_shape(client, monkeypatch):
    """The youtube_cookies field is optional — omitting it must behave
    exactly like it did before the field was added."""
    _override_actor(_owner_actor())

    def _explode(*_a, **_k):
        raise RuntimeError("reached DB path")

    monkeypatch.setattr(routes_module, "initial_video_key", lambda url: "vid")
    monkeypatch.setattr(routes_module, "current_owner", _explode)
    resp = client.post("/jobs", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert resp.status_code == 500  # past the cookie gate, blew up in DB path


