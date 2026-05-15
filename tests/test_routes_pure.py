"""Pure-validation route tests — no DB.

`extract_video_id` raises before the request handler ever touches the
session, so 422-on-bad-URL is testable without `SCRIBE_TEST_DATABASE_URL`.
This was previously bundled into `test_routes_dedup.py` and got skipped
on local runs that didn't have Postgres."""
from __future__ import annotations

from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.main import app


def _no_db_session():
    """Stand-in for `get_session`: yields a sentinel that fails loudly if
    the route ever touches it. The 422 path raises before consuming the
    session, so this is harmless for the test we run here."""
    class _Forbidden:
        def __getattr__(self, name): raise RuntimeError(f"db_session.{name} touched in pure test")
    yield _Forbidden()


def test_post_jobs_invalid_url_returns_422():
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        client = TestClient(app)
        resp = client.post("/jobs", json={"url": "not-a-youtube-url"})
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def test_post_jobs_invalid_callback_url_returns_422():
    """Pydantic AnyHttpUrl on JobCreate.callback_url rejects malformed
    values at the API boundary. Without this, the bad URL would reach
    `_deliver_webhook` and raise ValueError out of "never raises"."""
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        client = TestClient(app)
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "callback_url": "not-a-url"},
        )
        assert resp.status_code == 422
        # Pydantic surfaces the rejection on the right field, not the YT URL one.
        body = resp.json()
        assert any("callback_url" in loc for err in body["detail"] for loc in err["loc"])
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
