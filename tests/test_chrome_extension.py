from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension" / "chrome"


def read(path: str) -> str:
    return (EXTENSION / path).read_text(encoding="utf-8")


def test_manifest_v3_extension_contract() -> None:
    manifest = json.loads(read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "service_worker.js"
    assert manifest["options_page"] == "options.html"
    assert manifest["action"]["default_title"] == "Submit video to Scribe"
    assert {
        "activeTab",
        "alarms",
        "contextMenus",
        "cookies",
        "notifications",
        "storage",
    } <= set(manifest["permissions"])
    assert "https://scribe.oklabs.uk/*" in manifest["host_permissions"]
    # Default install must remain lean — youtube host permission is opt-in.
    assert "https://*.youtube.com/*" not in manifest["host_permissions"]
    assert "https://www.youtube.com/*" not in manifest["host_permissions"]
    assert "https://*.youtube.com/*" in manifest["optional_host_permissions"]
    assert "https://*/*" in manifest["optional_host_permissions"]
    assert manifest["icons"]["16"] == "icons/scribe-16.png"
    assert manifest["icons"]["48"] == "icons/scribe-48.png"
    assert manifest["icons"]["128"] == "icons/scribe-128.png"
    assert (EXTENSION / "icons" / "scribe-16.png").is_file()
    assert (EXTENSION / "icons" / "scribe-48.png").is_file()
    assert (EXTENSION / "icons" / "scribe-128.png").is_file()


def test_service_worker_uses_existing_jobs_api_and_video_flows() -> None:
    source = read("service_worker.js")

    assert 'const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";' in source
    assert 'const SOURCE = "chrome-extension";' in source
    assert 'fetch(`${config.baseUrl}/jobs`' in source
    assert "const payload = { url, source: SOURCE };" in source
    assert "JSON.stringify(payload)" in source
    assert "chrome.action.onClicked.addListener" in source
    assert "isSubmittableUrl(tab.url || \"\")" in source
    assert "HTTP_URL.test" in source
    assert "chrome.contextMenus.onClicked.addListener" in source
    assert 'id: "submit-page"' in source
    assert 'id: "submit-link"' in source
    assert "info.linkUrl || info.pageUrl" in source


def test_service_worker_reports_success_dedup_and_errors() -> None:
    source = read("service_worker.js")

    assert 'result.deduplicated ? "Already known to Scribe" : "Submitted to Scribe"' in source
    assert 'throw new Error("Scribe responded OK but returned no job ID.");' in source
    assert 'const jobUrl = `${baseUrl}/#/jobs/${result.job_id}`;' in source
    assert "chrome.notifications.onClicked.addListener" in source
    assert 'const NOTIFICATION_ICON = "icons/scribe-128.png";' in source
    assert "Could not reach Scribe" in source
    assert "Scribe rejected the URL" in source
    assert "formatHttpError(response.status, body, Boolean(config.bearerToken))" in source
    assert "formatDetail" in source
    assert "chrome.alarms.create(CLEAR_BADGE_ALARM" in source
    assert "setTimeout" not in source
    assert "chrome.permissions.request" not in source


def test_service_worker_sends_authorization_header_only_when_token_configured() -> None:
    source = read("service_worker.js")

    assert 'bearerToken: String(stored.bearerToken || "").trim()' in source
    assert "if (config.bearerToken) {" in source
    assert "headers.Authorization = `Bearer ${config.bearerToken}`;" in source
    assert 'bearerToken: "",' in source


def test_service_worker_formats_401_and_403_auth_errors_for_notifications() -> None:
    source = read("service_worker.js")

    assert "function formatHttpError(status, body, tokenConfigured)" in source
    assert "status === 401" in source
    assert "Scribe authentication required (401)" in source
    assert "This Scribe URL requires authentication" in source
    assert "The saved bearer token was rejected" in source
    assert "status === 403" in source
    assert "Scribe authorization failed (403)" in source
    assert "This Scribe URL is protected" in source
    assert "The saved bearer token is invalid or does not allow this request" in source


def test_options_store_base_url_and_optional_bearer_token_without_hardcoded_secret() -> None:
    html = read("options.html")
    source = read("options.js")

    assert 'id="base-url"' in html
    assert 'id="bearer-token"' in html
    assert 'type="password"' in html
    assert "Create a Chrome extension token in Scribe Settings" in html
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
    assert "Right-click a video link" in docs
    assert "A bearer token is required when the configured Scribe URL is protected" in docs
    assert "outside a trusted LAN" in docs
    assert "401/403 notification explains that auth is required" in docs
    assert "invalid bearer token" in docs
    assert "unreachable host" in docs
    assert "{Scribe base URL}/#/jobs/{job_id}" in docs
    assert "POST /jobs" in docs


def test_service_worker_collects_youtube_cookies_on_submit_without_caching() -> None:
    source = read("service_worker.js")

    assert 'importScripts("cookies.js")' in source
    assert "function isYoutubeUrl" in source
    assert "function collectYoutubeCookies" in source
    # Cookies are refreshed on every submit — no caching key, no storage write.
    assert "chrome.cookies.getAll" in source
    assert 'domain: ".youtube.com"' in source
    assert "isYoutubeUrl(url)" in source
    assert "payload.youtube_cookies = cookies" in source
    # Permission gate: the user must grant the optional host permission first.
    assert "chrome.permissions.contains" in source
    assert "https://*.youtube.com/*" in source
    # No cookie storage: never write cookie payloads back to chrome.storage.
    assert "chrome.storage.local.set({ cookies" not in source
    assert "chrome.storage.sync.set({ cookies" not in source


def test_service_worker_never_logs_cookie_values_or_names() -> None:
    sw = read("service_worker.js")
    cookies_js = read("cookies.js")

    # No console.log anywhere — we never want cookie values reaching devtools.
    for module_source in (sw, cookies_js):
        assert "console.log" not in module_source
        assert "console.debug" not in module_source
        assert "console.info" not in module_source


def test_cookies_module_serializer_contract() -> None:
    source = read("cookies.js")

    assert "function serializeCookiesToNetscape" in source
    assert "# Netscape HTTP Cookie File" in source
    assert "#HttpOnly_" in source
    # The serializer must use TAB delimiters per the Netscape format spec.
    assert "\\t" in source
    # Tabs and newlines in cookie names/values would corrupt the file —
    # the serializer must reject those lines.
    assert "containsTabOrNewline" in source


def test_options_page_exposes_youtube_cookie_grant_and_revoke() -> None:
    html = read("options.html")
    source = read("options.js")

    assert 'id="grant-youtube"' in html
    assert 'id="revoke-youtube"' in html
    assert "YouTube cookies" in html
    assert "https://*.youtube.com/*" in source
    assert "chrome.permissions.request" in source
    assert "chrome.permissions.remove" in source


def test_extension_docs_describe_youtube_cookie_flow() -> None:
    docs = read("README.md")

    assert "YouTube cookies" in docs
    assert "Enable YouTube cookies" in docs
    assert "https://*.youtube.com/*" in docs
    assert "youtube_cookies" in docs
    assert "never stored" in docs.lower() or "nothing is cached" in docs


# ---------------------------------------------------------------------------
# Netscape serializer unit tests (run the JS under bun).
# ---------------------------------------------------------------------------

_BUN_BIN = shutil.which("bun")


def _run_serializer(cookies: list) -> str:
    """Execute serializeCookiesToNetscape via bun and return the result."""
    if _BUN_BIN is None:
        pytest.skip("bun is required to run the cookies.js serializer test")
    script = (
        f'const {{ serializeCookiesToNetscape }} = require("{EXTENSION / "cookies.js"}");\n'
        f"const cookies = {json.dumps(cookies)};\n"
        "process.stdout.write(serializeCookiesToNetscape(cookies));\n"
    )
    proc = subprocess.run(
        [_BUN_BIN, "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_serializer_empty_input_returns_empty_string() -> None:
    assert _run_serializer([]) == ""


def test_serializer_emits_netscape_header_and_tab_fields() -> None:
    out = _run_serializer(
        [
            {
                "name": "LOGIN_INFO",
                "value": "opaque",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "hostOnly": False,
                "session": False,
                "expirationDate": 2147483647.0,
            },
            {
                "name": "VISITOR_INFO1_LIVE",
                "value": "abc123",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "hostOnly": False,
                "session": True,
            },
        ],
    )
    assert out.startswith("# Netscape HTTP Cookie File")
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("# ")]
    assert lines == [
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\topaque",
        ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tabc123",
    ]


def test_serializer_handles_host_only_cookie_without_leading_dot() -> None:
    out = _run_serializer(
        [
            {
                "name": "PREF",
                "value": "v",
                "domain": "youtube.com",
                "path": "/",
                "secure": False,
                "httpOnly": False,
                "hostOnly": True,
                "session": False,
                "expirationDate": 1900000000.0,
            },
        ],
    )
    body = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert body == ["youtube.com\tFALSE\t/\tFALSE\t1900000000\tPREF\tv"]


def test_serializer_skips_cookies_with_tabs_or_newlines() -> None:
    out = _run_serializer(
        [
            {
                "name": "OK",
                "value": "good",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "hostOnly": False,
                "session": False,
                "expirationDate": 2000000000,
            },
            {
                "name": "BAD\tNAME",
                "value": "anything",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "hostOnly": False,
                "session": False,
                "expirationDate": 2000000000,
            },
            {
                "name": "ALSO_BAD",
                "value": "line\nbreak",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "hostOnly": False,
                "session": False,
                "expirationDate": 2000000000,
            },
        ],
    )
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert lines == [".youtube.com\tTRUE\t/\tTRUE\t2000000000\tOK\tgood"]
