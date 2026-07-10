"""POST /jobs/upload gating: 503 unconfigured, 413 oversize, 422 empty/invalid.

DB-free: every path here is reached before the route touches the session
(config gate, size cap, ffprobe validation), so the session is a forbidden
stub. The full archiving flow is exercised in test_worker_archiving.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.api.auth import Actor
from scribe.config import settings
from scribe.main import app
from scribe.pipeline import ffmpeg


def _no_db_session():
    class _Forbidden:
        def __getattr__(self, name):
            raise RuntimeError(f"db_session.{name} touched in pure test")
    yield _Forbidden()


def _owner_actor() -> Actor:
    return Actor(
        kind="extension", role="user", subject="user_x",
        user_id=42, owner_id=7, email="owner@example.com", display_name="Owner",
    )


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Staging writes land in a writable tmp dir.
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    app.dependency_overrides[routes_module.require_actor] = _owner_actor
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        app.dependency_overrides.pop(routes_module.require_actor, None)


def _configure_media(monkeypatch):
    monkeypatch.setattr(routes_module.media_store, "is_configured", lambda: True)


def test_upload_503_when_media_unconfigured(client, monkeypatch):
    # Default settings leave media creds empty → feature off.
    monkeypatch.setattr(routes_module.media_store, "is_configured", lambda: False)
    r = client.post("/jobs/upload", files={"file": ("v.mp4", b"data", "video/mp4")})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_upload_413_when_oversize(client, monkeypatch):
    _configure_media(monkeypatch)
    monkeypatch.setattr(settings, "upload_max_bytes", 16)
    r = client.post("/jobs/upload", files={"file": ("v.mp4", b"x" * 1024, "video/mp4")})
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]


def test_upload_422_when_empty(client, monkeypatch):
    _configure_media(monkeypatch)
    r = client.post("/jobs/upload", files={"file": ("v.mp4", b"", "video/mp4")})
    assert r.status_code == 422
    assert "empty" in r.json()["detail"]


def test_upload_422_when_not_media(client, monkeypatch):
    _configure_media(monkeypatch)

    def bad_probe(_path):
        raise ffmpeg.FfmpegError("upload has no decodable audio or video stream")

    monkeypatch.setattr(routes_module.ffmpeg, "probe_media", bad_probe)
    r = client.post("/jobs/upload", files={"file": ("junk.mp4", b"not a real video", "video/mp4")})
    assert r.status_code == 422
    assert "invalid media file" in r.json()["detail"]


def test_upload_422_when_summary_prompt_too_long(client, monkeypatch):
    # summary_prompt is capped at SUMMARY_PROMPT_MAX_CHARS just like the JSON
    # POST /jobs contract, so a multipart upload can't smuggle an oversize
    # prompt past the limit. Rejected at request parsing (422) before the
    # session is touched — hence DB-free.
    from scribe.api.schemas import SUMMARY_PROMPT_MAX_CHARS

    _configure_media(monkeypatch)
    r = client.post(
        "/jobs/upload",
        files={"file": ("v.mp4", b"data", "video/mp4")},
        data={"summary_prompt": "x" * (SUMMARY_PROMPT_MAX_CHARS + 1)},
    )
    assert r.status_code == 422


def test_upload_requires_auth(monkeypatch, tmp_path):
    # No require_actor override + non-trusted client → 401 like POST /jobs.
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    monkeypatch.setattr(routes_module.media_store, "is_configured", lambda: True)
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post(
            "/jobs/upload",
            files={"file": ("v.mp4", b"data", "video/mp4")},
            headers={"x-forwarded-for": "8.8.8.8"},
        )
        assert r.status_code == 401
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
