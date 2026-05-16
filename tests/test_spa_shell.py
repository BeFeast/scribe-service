from __future__ import annotations

import json

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

    client = TestClient(app)
    response = client.get("/__spa__/")

    assert response.status_code == 200
    assert "<title>Scribe SPA</title>" in response.text
    assert '<div id="root"></div>' in response.text
    assert 'href="/static/spa/assets/index-def456.css"' in response.text
    assert 'src="/static/spa/assets/index-abc123.js"' in response.text
    assert "babel" not in response.text.lower()
    assert "unpkg" not in response.text.lower()
    assert "cdn" not in response.text.lower()
