from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension" / "chrome"


def read(path: str) -> str:
    return (EXTENSION / path).read_text(encoding="utf-8")


def test_manifest_v3_extension_contract() -> None:
    manifest = json.loads(read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "service_worker.js"
    assert manifest["options_page"] == "options.html"
    assert manifest["action"]["default_title"] == "Submit YouTube video to Scribe"
    assert {"activeTab", "alarms", "contextMenus", "notifications", "storage"} <= set(
        manifest["permissions"],
    )
    assert "https://scribe.oklabs.uk/*" in manifest["host_permissions"]
    assert "https://www.youtube.com/*" in manifest["host_permissions"]
    assert "https://*/*" in manifest["optional_host_permissions"]
    assert manifest["icons"]["16"] == "icons/scribe-16.png"
    assert manifest["icons"]["48"] == "icons/scribe-48.png"
    assert manifest["icons"]["128"] == "icons/scribe-128.png"
    assert (EXTENSION / "icons" / "scribe-16.png").is_file()
    assert (EXTENSION / "icons" / "scribe-48.png").is_file()
    assert (EXTENSION / "icons" / "scribe-128.png").is_file()


def test_service_worker_uses_existing_jobs_api_and_youtube_flows() -> None:
    source = read("service_worker.js")

    assert 'const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";' in source
    assert 'const SOURCE = "chrome-extension";' in source
    assert 'fetch(`${config.baseUrl}/jobs`' in source
    assert "JSON.stringify({ url, source: SOURCE })" in source
    assert "chrome.action.onClicked.addListener" in source
    assert "YOUTUBE_WATCH_URL.test(tab.url)" in source
    assert "(?:[^/]+\\.)?(?:youtube\\.com\\/|youtu\\.be\\/)" in source
    assert "chrome.contextMenus.onClicked.addListener" in source
    assert 'id: "submit-page"' in source
    assert 'id: "submit-link"' in source
    assert "info.linkUrl || info.pageUrl" in source


def test_service_worker_reports_success_dedup_and_errors() -> None:
    source = read("service_worker.js")

    assert 'result.deduplicated ? "Already known to Scribe" : "Submitted to Scribe"' in source
    assert 'throw new Error("Scribe responded OK but returned no job ID.");' in source
    assert 'const jobUrl = `${baseUrl}/__spa__/#/jobs/${result.job_id}`;' in source
    assert "chrome.notifications.onClicked.addListener" in source
    assert 'const NOTIFICATION_ICON = "icons/scribe-128.png";' in source
    assert "Could not reach Scribe" in source
    assert "Scribe rejected the URL" in source
    assert "formatDetail" in source
    assert "chrome.alarms.create(CLEAR_BADGE_ALARM" in source
    assert "setTimeout" not in source
    assert "chrome.permissions.request" not in source


def test_options_store_base_url_and_optional_bearer_token_without_hardcoded_secret() -> None:
    html = read("options.html")
    source = read("options.js")

    assert 'id="base-url"' in html
    assert 'id="bearer-token"' in html
    assert 'type="password"' in html
    assert 'const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";' in source
    assert "chrome.storage.sync.get" in source
    assert "chrome.storage.sync.set" in source
    assert "chrome.permissions.request" in source
    assert "bearerToken: bearerTokenInput.value.trim()" in source
    assert "sk-" not in source
    assert "ghp_" not in source


def test_extension_docs_include_install_and_manual_verification_checklist() -> None:
    docs = read("README.md")

    assert "No build step is required." in docs
    assert "Load unpacked" in docs
    assert "Manual Verification" in docs
    assert "toolbar action" in docs
    assert "Right-click a YouTube link" in docs
    assert "unreachable host" in docs
    assert "POST /jobs" in docs
