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
    assert (
        '<html lang="en" data-variant="field" data-theme="light" '
        'data-density="compact" data-library-layout="feed">'
    ) in response.text
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in response.text
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in response.text
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in response.text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in response.text
    assert '<div id="root"></div>' in response.text
    assert 'href="/static/spa/assets/index-def456.css"' in response.text
    assert 'src="/static/spa/assets/index-abc123.js"' in response.text
    assert "babel" not in response.text.lower()
    assert "unpkg" not in response.text.lower()
    assert "cdn" not in response.text.lower()

    alias_response = client.get("/__spa__/")
    assert alias_response.status_code == 200
    assert '<div id="root"></div>' in alias_response.text


def test_favicon_route_serves_icon_response():
    client = TestClient(app)

    ico = client.get("/favicon.ico")
    assert ico.status_code == 200
    assert ico.headers["content-type"] == "image/x-icon"
    assert ico.content[:4] == b"\x00\x00\x01\x00"

    svg = client.get("/favicon.svg")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in svg.text
    assert "#657153" in svg.text

    for path, ctype in (
        ("/apple-touch-icon.png", "image/png"),
        ("/icon-192.png", "image/png"),
        ("/icon-512.png", "image/png"),
        ("/icon-maskable-512.png", "image/png"),
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"] == ctype
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["name"] == "Scribe"
    assert data["theme_color"] == "#657153"
    icons = {icon["src"] for icon in data["icons"]}
    assert {"/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"} <= icons


def test_extension_icons_match_brand_and_diverge_from_placeholder():
    """Extension PNGs must be the sage Field brand mark, no longer byte-identical to the old
    karaoke-shared blue placeholder."""
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ext = root / "extension" / "chrome" / "icons"
    brand = root / "src" / "scribe" / "web" / "static" / "brand"

    placeholder_hashes = {
        # sha256 of the pre-#319 blue placeholder PNGs (identical to karaoke's).
        "scribe-16.png": "d2fdf18c22d214e65d5e4028ef58c6f8d9922ad994cf3323f8f67546fb408a57",
        "scribe-48.png": "ee841ce9547d6853f0825b3ac5f5bfdba88aa3044fa3215687115abb97d7aed2",
        "scribe-128.png": "2e290788c26d7f3f9a5a99fb4c4627def8826e389f63c95f353f7aceca9ea726",
    }
    for name, old in placeholder_hashes.items():
        digest = hashlib.sha256((ext / name).read_bytes()).hexdigest()
        assert digest != old, f"{name} still matches the karaoke-shared placeholder"

    svg = (ext / "scribe.svg").read_text(encoding="utf-8")
    assert "#657153" in svg, "extension svg must use the Field sage stroke"
    assert "#174ea6" not in svg, "extension svg must not use the placeholder blue"

    assert (ext / "scribe.svg").read_bytes() == (brand / "scribe.svg").read_bytes()


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
