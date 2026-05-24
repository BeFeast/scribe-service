"""Pure-validation route tests — no DB."""
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

