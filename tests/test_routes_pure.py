"""Pure-validation route tests — no DB."""
from __future__ import annotations

import pytest
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
        # Pydantic surfaces the rejection on the right field, not the media URL one.
        body = resp.json()
        assert any("callback_url" in loc for err in body["detail"] for loc in err["loc"])
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def test_job_create_defaults_preserve_legacy_behavior():
    """Omitting the #296 toggles must keep today's defaults so existing
    {url, source} callers are byte-compatible: summarize/notify on, no prompt."""
    from scribe.api.schemas import JobCreate

    body = JobCreate(url="https://youtu.be/dQw4w9WgXcQ")
    assert body.summarize is True
    assert body.notify is True
    assert body.summary_prompt is None


def test_job_create_accepts_optional_toggles():
    """JobCreate parses the three optional #296 fields."""
    from scribe.api.schemas import JobCreate

    body = JobCreate(
        url="https://youtu.be/dQw4w9WgXcQ",
        summarize=False,
        notify=False,
        summary_prompt="One-line gist only.",
    )
    assert body.summarize is False
    assert body.notify is False
    assert body.summary_prompt == "One-line gist only."


def test_job_create_accepts_direct_media_url():
    """A direct HTTP(S) media URL is accepted the same way a YouTube URL is,
    producing the same JobCreate shape (#416)."""
    from scribe.api.schemas import JobCreate

    body = JobCreate(url="https://cdn.example.com/media/clip.mp4")
    assert body.url == "https://cdn.example.com/media/clip.mp4"


def test_job_create_strips_surrounding_whitespace_on_url():
    """Surrounding whitespace is trimmed so a pasted URL is normalized once at
    the boundary."""
    from scribe.api.schemas import JobCreate

    body = JobCreate(url="  https://cdn.example.com/a.mp3  ")
    assert body.url == "https://cdn.example.com/a.mp3"


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a.mp4",
        "data:audio/mp3;base64,AAAA",
        "javascript:alert(1)",
        "not-a-url",
        "https://",
        "",
    ],
)
def test_job_create_rejects_unsafe_or_malformed_url(bad_url):
    """Non-HTTP(S) schemes and host-less/empty values are rejected before a Job
    row is ever created (#416)."""
    from pydantic import ValidationError

    from scribe.api.schemas import JobCreate

    with pytest.raises(ValidationError):
        JobCreate(url=bad_url)


def test_post_jobs_rejects_unsafe_url_returns_422():
    """A non-HTTP(S) scheme is rejected at the API boundary with a 422 on the
    url field, before the route touches the DB."""
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        client = TestClient(app)
        resp = client.post("/jobs", json={"url": "file:///etc/passwd"})
        assert resp.status_code == 422
        body = resp.json()
        assert any("url" in loc for err in body["detail"] for loc in err["loc"])
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def test_post_jobs_rejects_non_bool_summarize_returns_422():
    """A non-boolean summarize is rejected at the API boundary before the
    route touches the DB."""
    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        client = TestClient(app)
        resp = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "summarize": "maybe"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert any("summarize" in loc for err in body["detail"] for loc in err["loc"])
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def test_post_jobs_rejects_oversize_summary_prompt_returns_422():
    """summary_prompt is capped at SUMMARY_PROMPT_MAX_CHARS so a runaway prompt
    is rejected at the boundary, not silently forwarded to the summarizer."""
    from scribe.api.schemas import SUMMARY_PROMPT_MAX_CHARS

    app.dependency_overrides[routes_module.get_session] = _no_db_session
    try:
        client = TestClient(app)
        resp = client.post(
            "/jobs",
            json={
                "url": "https://youtu.be/dQw4w9WgXcQ",
                "summary_prompt": "x" * (SUMMARY_PROMPT_MAX_CHARS + 1),
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert any("summary_prompt" in loc for err in body["detail"] for loc in err["loc"])
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)


def test_system_snapshot_formats_vast_spend_in_display_currency(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "display_currency", "ILS")
    rows = routes_module._system_snapshot(
        backup=routes_module.BackupSnapshot(
            last_success_iso=None,
            age_seconds=30,
            stale_after=90_000,
            stale=False,
            path="/tmp/backup-heartbeat",
        ),
        worker_pool=routes_module.WorkerPoolSnapshot(active=0, total=2),
        worker_active_count=0,
        spend_24h=0.072,
        daily_cap=0.0,
    )
    vast = next(row for row in rows if row.label == "Vast.ai")

    assert "₪0.27 ILS" in vast.value
    assert "$0.07" not in vast.value

