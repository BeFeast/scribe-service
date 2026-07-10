"""Static contract guard for the client-side capture spike PoC (#413).

The PoC in ``extension/chrome-client-capture-poc/`` demonstrates the de-risked
half of the extension acquisition path (offscreen ranged download + chunked
upload to the existing ``POST /jobs/upload``). We can't drive a real browser
here, so — like ``test_chrome_extension.py`` — we assert the static shape: the
MV3 topology, the CORS-bypass host permission, the upload target, and that the
unsolved stream-resolution seam fails loudly instead of pretending to work.

This test also guards isolation: the PoC must not disturb the shipping
extension in ``extension/chrome/``.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "extension" / "chrome-client-capture-poc"


def read(name: str) -> str:
    return (POC / name).read_text(encoding="utf-8")


def test_poc_is_isolated_from_shipping_extension() -> None:
    # The PoC lives in its own directory so it can't affect the production
    # extension's manifest/permissions/tests.
    assert POC.is_dir()
    assert POC != ROOT / "extension" / "chrome"
    assert (ROOT / "extension" / "chrome" / "manifest.json").is_file()


def test_manifest_mv3_offscreen_and_cors_bypass() -> None:
    manifest = json.loads(read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "service_worker.js"

    # Offscreen permission is what lets us hold a large Blob outside the SW.
    assert "offscreen" in manifest["permissions"]

    # The whole premise of path A: host permission for googlevideo bypasses the
    # page CORS wall a plain web app can't get past (findings §6).
    assert "https://*.googlevideo.com/*" in manifest["host_permissions"]
    # Innertube resolution needs youtube; upload needs the Scribe origin.
    assert "https://www.youtube.com/*" in manifest["host_permissions"]
    assert any(h.startswith("https://scribe.") for h in manifest["host_permissions"])

    # It must be obviously a spike, not mistaken for the shipping extension.
    assert "SPIKE" in manifest["name"].upper() or "PoC" in manifest["name"]


def test_service_worker_offscreen_topology() -> None:
    source = read("service_worker.js")

    # SW orchestrates via the offscreen document (it never holds the bytes).
    assert "chrome.offscreen.createDocument" in source
    assert "offscreen.html" in source
    assert 'reasons: ["BLOBS"]' in source
    # Resolution is delegated to the seam, not inlined/faked here.
    assert "resolveAudioStream" in source


def test_offscreen_downloads_ranged_and_uploads_to_existing_endpoint() -> None:
    source = read("offscreen.js")

    # Memory-safe download: HTTP range requests, not one giant buffer.
    assert "Range" in source
    assert "bytes=" in source

    # Upload targets the EXISTING server endpoint (#408), as multipart, and must
    # NOT hand-set Content-Type (the browser sets the multipart boundary).
    assert "/jobs/upload" in source
    assert "FormData" in source
    assert 'form.append("file"' in source
    assert "Authorization" in source


def test_resolution_seam_fails_loudly_not_silently() -> None:
    # The unsolved part must throw, so the PoC can't masquerade as a working
    # end-to-end path (findings §2/§9). A silent stub would be a false green.
    source = read("resolve.js")
    assert "throw new Error" in source
    assert "resolveAudioStream" in source


def test_readme_documents_status_and_manual_step() -> None:
    readme = read("README.md")
    lower = readme.lower()
    # It must be explicit that E2E is manual and that resolution is unsolved.
    assert "manual" in lower
    assert "/jobs/upload" in readme
    assert "spike" in lower
