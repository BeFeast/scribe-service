from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scribe.main import app
from scribe.web import views


def test_spa_shell_uses_vite_manifest(monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/index-abc123.js",
                    "css": ["assets/index-def456.css"],
                    "isEntry": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(views, "_SPA_MANIFEST_PATH", manifest_path)
    views._spa_asset_urls.cache_clear()

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Scribe SPA</title>" in response.text
    assert '<div id="root"></div>' in response.text
    assert 'href="/static/spa/assets/index-def456.css"' in response.text
    assert 'src="/static/spa/assets/index-abc123.js"' in response.text
    assert "babel" not in response.text.lower()
    assert "unpkg" not in response.text.lower()
    assert "cdn" not in response.text.lower()

    alias_response = client.get("/__spa__/")
    assert alias_response.status_code == 200
    assert '<div id="root"></div>' in alias_response.text


def test_classic_transcript_list_remains_explicit():
    rows = [
        SimpleNamespace(
            id=7,
            title="Classic List Entry",
            tags=["legacy"],
            lang="en",
            duration_seconds=125,
            created_at=dt.datetime(2026, 5, 16, 9, 30),
        )
    ]

    class FakeScalars:
        def all(self):
            return rows

    class FakeSession:
        def scalars(self, _stmt):
            return FakeScalars()

    app.dependency_overrides[views.get_session] = lambda: FakeSession()
    try:
        response = TestClient(app).get("/classic")
    finally:
        app.dependency_overrides.pop(views.get_session, None)

    assert response.status_code == 200
    assert "Classic List Entry" in response.text
    assert 'action="/classic"' in response.text
    assert 'href="/classic?tag=legacy"' in response.text
