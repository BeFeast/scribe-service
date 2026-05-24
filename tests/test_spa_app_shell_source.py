from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"
DESIGN_EXPORT = ROOT / "design" / "scribe-redesign-2026-05-24" / "app"
STAGED_SOURCE = SPA_SRC / "design-source" / "app"
DESIGN_APP = SPA_SRC / "design-app"
DESIGN_ARCHIVE = Path("/mnt/storage/src/Scribe.redesign.zip")
EXPECTED_DESIGN_ARCHIVE_SHA256 = (
    "3253d4d262b00a25bdb07bf4ff3c7112998b9b8ee917211438aa220bcdd9719a"
)
DESIGN_SOURCE_FILES = (
    "app.jsx",
    "command-palette.jsx",
    "data.jsx",
    "icons.jsx",
    "job-pages.jsx",
    "library.jsx",
    "ops.jsx",
    "settings.jsx",
    "shell.jsx",
    "styles.css",
    "transcript-detail.jsx",
    "tweaks-panel.jsx",
)


def read(path: str) -> str:
    return (SPA_SRC / path).read_text(encoding="utf-8")


def production_sources() -> str:
    parts: list[str] = []
    for path in SPA_SRC.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".jsx", ".ts", ".tsx", ".css"}:
            if "design-source" in path.parts:
                continue
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_design_export_source_is_staged_verbatim() -> None:
    staged_names = sorted(path.name for path in STAGED_SOURCE.glob("*") if path.is_file())
    export_names = sorted(path.name for path in DESIGN_EXPORT.glob("*") if path.is_file())

    assert staged_names == sorted(DESIGN_SOURCE_FILES)
    assert export_names == sorted(DESIGN_SOURCE_FILES)
    for name in DESIGN_SOURCE_FILES:
        assert (STAGED_SOURCE / name).read_text(encoding="utf-8") == (
            DESIGN_EXPORT / name
        ).read_text(encoding="utf-8")


def test_staged_design_source_matches_claude_archive_when_available() -> None:
    if not DESIGN_ARCHIVE.exists():
        pytest.skip("Design archive not available at /mnt/storage/src/Scribe.redesign.zip")

    digest = hashlib.sha256(DESIGN_ARCHIVE.read_bytes()).hexdigest()
    assert digest == EXPECTED_DESIGN_ARCHIVE_SHA256

    with zipfile.ZipFile(DESIGN_ARCHIVE) as archive:
        archive_names = sorted(
            name.removeprefix("app/")
            for name in archive.namelist()
            if name.startswith("app/") and not name.endswith("/")
        )
        assert archive_names == sorted(DESIGN_SOURCE_FILES)
        for name in DESIGN_SOURCE_FILES:
            expected = archive.read(f"app/{name}").decode("utf-8")
            assert (STAGED_SOURCE / name).read_text(encoding="utf-8") == expected
            assert (DESIGN_EXPORT / name).read_text(encoding="utf-8") == expected


def test_old_visual_spa_source_was_removed_from_production_path() -> None:
    for path in (
        "DesignSystemPlayground.tsx",
        "components/CommandPalette.tsx",
        "components/ConfirmDialog.tsx",
        "components/FailureRow.tsx",
        "components/JobCard.tsx",
        "components/LogTail.tsx",
        "components/Markdown.tsx",
        "components/PipelineDiagram.tsx",
        "components/PrivateShareLinks.tsx",
        "components/ShellIcons.tsx",
        "components/Sidebar.tsx",
        "components/StatusChip.tsx",
        "components/TopBar.tsx",
        "pages/JobDetail.tsx",
        "pages/Library.tsx",
        "pages/Ops.tsx",
        "pages/Queue.tsx",
        "pages/Settings.tsx",
        "pages/Transcript.tsx",
        "shareTargets.ts",
    ):
        assert not (SPA_SRC / path).exists()

    for glue in (
        "hooks/useAuth.tsx",
        "hooks/useRoute.ts",
        "hooks/useTweaks.ts",
        "hooks/usePoll.ts",
        "hooks/useEventSource.ts",
        "lib/auth.ts",
        "lib/currency.ts",
    ):
        assert (SPA_SRC / glue).is_file()


def test_app_mounts_design_app_with_auth_route_and_tweaks_glue() -> None:
    main = read("main.jsx")
    index = (ROOT / "web" / "spa" / "index.html").read_text(encoding="utf-8")

    assert '<script type="module" src="/src/main.jsx"></script>' in index
    assert "AuthProvider" in main
    assert "useAuth" in main
    assert "useRoute" in main
    assert "useTweaks" in main
    assert "useScribeRuntime" in main
    assert "setRuntimeData" in main
    for module in (
        "./design-app/library.jsx",
        "./design-app/transcript-detail.jsx",
        "./design-app/job-pages.jsx",
        "./design-app/ops.jsx",
        "./design-app/settings.jsx",
        "./design-app/shell.jsx",
        "./design-app/command-palette.jsx",
    ):
        assert module in main
    for route in ('case "transcript"', 'case "queue"', 'case "job"', 'case "ops"', 'case "settings"'):
        assert route in main
    assert "TweaksPanel" not in main


def test_design_app_modules_keep_exported_route_structure() -> None:
    expected = {
        "library.jsx": ("export function LibraryPage", "function LibTable", "function LibFeed", "function LibCards", "function InFlightStrip"),
        "transcript-detail.jsx": ("export function TranscriptDetail", "function ShareSheet", "function PartialNotice", "function Markdown"),
        "job-pages.jsx": ("export function QueuePage", "export function JobDetail", "export function PipelineDiagram", "export function FailureRow"),
        "ops.jsx": ("export function OpsPage", "function Sparkline", "function StatusBars", "function SystemRow"),
        "settings.jsx": ("export function SettingsPage", "function AccessGroup", "function UserRow"),
        "shell.jsx": ("export function TopBar", "export function Sidebar"),
        "command-palette.jsx": ("export function CommandPalette", "Submit job", "Recent submissions"),
    }
    for file_name, markers in expected.items():
        source = (DESIGN_APP / file_name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source
        assert "../components" not in source
        assert "../pages" not in source


def test_adapter_contract_is_non_visual_and_owns_backend_translation() -> None:
    adapters = read("design-app/adapters.js")
    api = read("design-app/api.jsx")

    for marker in (
        "export function adaptLibraryRow",
        "export function adaptTranscript",
        "export function adaptJob",
        "export function adaptFailure",
        "export function adaptOps",
        "export function adaptUsers",
    ):
        assert marker in adapters
    for forbidden in ("className", "style=", "gridTemplate", "fontFamily", "--accent", "border:"):
        assert forbidden not in adapters
    for endpoint in (
        '"/api/library?limit=100"',
        '"/api/jobs/active"',
        '"/api/jobs/recent-failures?limit=12"',
        '"/api/ops"',
        '"/api/jobs/" + id + "/log/stream"',
        '"/admin/jobs/" + id + "/cancel"',
        '"/admin/jobs/" + id + "/retry"',
        '"/api/auth/me"',
        '"/api/admin/users"',
        '"/transcripts/" + route.params.id',
        '"/jobs/" + route.params.id',
    ):
        assert endpoint in api
    assert "auth.protectedFetch" in api
    assert "auth.maybeAutoSignIn()" in api


def test_settings_appearance_is_the_only_variant_control_surface() -> None:
    settings = read("design-app/settings.jsx")
    source = production_sources()

    assert "<h2>Appearance</h2>" in settings
    assert "Field" in settings
    assert "Paper" in settings
    assert "Terminal" in settings
    assert "Console" in settings
    assert "Light" in settings
    assert "Dark" in settings
    assert '["compact","cozy","comfy"]' in settings
    assert "Choose the production appearance" in settings
    assert "floating Tweaks panel" not in settings
    assert "TweaksPanel" not in source
    assert ".tweaks-panel" not in source


def test_production_spa_polish_source_guards() -> None:
    source = production_sources()
    settings = read("design-app/settings.jsx")
    transcript = read("design-app/transcript-detail.jsx")

    assert "go.oklabs.uk" not in source
    assert "Transcript excerpt" not in transcript
    assert "<span>Transcript</span>" in transcript
    assert "display_currency" in settings
    assert "NIS / ILS" in settings
    assert "USD" in settings
    assert "EUR" in settings
    assert "public_base_url" in settings
    assert "/share/&lt;token&gt;" in settings


def test_usd_backed_spend_uses_shared_display_currency_conversion() -> None:
    data = read("design-app/data.js")
    currency = read("lib/currency.ts")
    settings = read("design-app/settings.jsx")
    source = production_sources()

    assert "convertUsdToDisplayCurrency as convertUsdAmount" in data
    assert "export function convertUsdToDisplayCurrency" in data
    assert "convertUsdAmount(Number(value), normalized)" in data
    assert "usdDisplayRates" in currency
    assert "convertUsdToDisplayCurrency(value, currency)" in currency
    assert "It does not perform FX conversion" not in source
    assert "Cap input saves canonical USD" in settings
    assert '<span className="muted mono" style={{fontSize: 12}}>USD</span>' in settings


def test_core_route_wiring_uses_real_backend_actions_where_present() -> None:
    transcript = read("design-app/transcript-detail.jsx")
    command = read("design-app/command-palette.jsx")
    settings = read("design-app/settings.jsx")
    api = read("design-app/api.jsx")

    assert 'auth.protectedFetch("/transcripts/" + t.id + "/resummarize"' in transcript
    assert 'auth.protectedFetch("/admin/transcripts/" + t.id' in transcript
    assert 'auth.protectedFetch("/api/transcripts/" + t.id + "/share-links"' in transcript
    assert 'copyFromEndpoint("/transcripts/" + t.id + "/summary.md"' in transcript
    assert 'copyFromEndpoint("/transcripts/" + t.id + "/transcript.md"' in transcript
    assert 'const path = "/transcripts/" + t.id + "/" + kind + ".md"' in transcript
    assert '<a onClick={() => navigate("library")}' in transcript
    assert '<button className="btn danger" onClick={() => void deleteTranscript()}' in transcript
    assert 'auth.protectedFetch("/jobs"' in command
    assert 'body: JSON.stringify({ url: videoUrl.url, source: "manual" })' in command
    assert "parseVideoUrl" in command
    assert "isJobView" in command
    assert 'fetchJson(auth, "/api/config"' in settings
    assert 'fetchJson(auth, "/api/prompts"' in settings
    assert 'fetchJson(auth, "/api/auth/extension-token"' in settings
    assert "navigator.clipboard.writeText(extensionTokenState.token)" in settings
    assert "/api/config/rotate-token" not in settings
    assert "configured server-side" not in settings
    assert "bearer-token rotation is not implemented yet" not in settings
    assert "POST /api/config/rotate-token is not implemented yet" not in settings
    assert "auth.signOut()" in settings
    assert "function clerkProfileAction()" in settings
    assert "openUserProfile" in settings
    assert "onClick={manageInClerk}" in settings
    assert "onClick={signOut}" in settings
    assert 'auth.protectedFetch("/api/prompts/" + promptVersion' in settings
    assert 'fetchJson(auth, "/api/admin/users"' in settings
    assert '"/api/admin/users/" + user.id + "/role"' in settings
    assert "SCRIBE_USERS" not in settings
    assert "fetchJson(auth" in api
    assert "auth.protectedFetch(\"/api/jobs/\" + id + \"/log/stream\"" in api
    assert "currentJob.value?.id !== route.params.id" in api
    assert "setTimeout(load, 2000)" in api
    assert "export function isInFlight" in api
    assert "isInFlight(job.status)" in read("design-app/job-pages.jsx")
    assert "onCancelJob" in read("design-app/job-pages.jsx")
    assert "onRetryJob" in read("design-app/job-pages.jsx")
    assert "STATS.system" in read("design-app/ops.jsx")


def test_settings_access_region_uses_auth_me_role_as_visibility_source() -> None:
    settings = read("design-app/settings.jsx")
    api = read("design-app/api.jsx")
    main = read("main.jsx")

    assert "currentUser: null" in api
    assert 'fetchJson(auth, "/api/auth/me", controller.signal)' in api
    assert 'if (body?.role !== "admin") return [];' in api
    assert 'return fetchJson(auth, "/api/admin/users", controller.signal);' in api
    assert "currentUser: me" in api
    assert 'setCore((previous) => ({ ...previous, currentUser: null, users: [] }));' in api
    assert 'if (!controller.signal.aborted) setCore((previous) => ({ ...previous, currentUser: me }));' in api
    assert 'if (!controller.signal.aborted) setCore((previous) => ({ ...previous, currentUser: me, users: [] }));' in api
    assert "currentUser: runtime.currentUser" in main
    assert "currentUser={runtime.currentUser}" in main
    assert "export function canRenderAccessGroup(currentUser)" in settings
    assert 'return currentUser?.role === "admin";' in settings
    assert (
        '{canRenderAccessGroup(currentUser) && <AccessGroup initialUsers={runtimeUsers}/>}'
        in settings
    )
    assert "canWrite" not in settings


def test_transcript_summary_frontmatter_renders_as_properties_panel() -> None:
    transcript = read("design-app/transcript-detail.jsx")
    styles = read("styles.css")

    assert "function splitFrontmatter" in transcript
    assert "function SummaryBody" in transcript
    assert "function PropertiesPanel" in transcript
    assert '<SummaryBody src={t.summary_md}' in transcript
    assert '<Markdown src={summary.body}/>' in transcript
    assert '<Markdown src={t.summary_md}/>' not in transcript
    assert "className={open ? \"fm-panel\" : \"fm-panel collapsed\"}" in transcript
    assert 'aria-label="Summary properties"' in transcript
    assert 'navigate("library", { tag: value.target })' in transcript
    assert 'navigate("library", { tag: stripHash(item) })' in transcript

    for marker in (
        ".fm-panel",
        ".fm-header",
        ".fm-row",
        ".fm-pill",
        ".fm-link",
        ".fm-tag",
        ".fm-copy",
        ".body-dimmed",
    ):
        assert marker in styles


def test_transcript_summary_copy_still_uses_full_markdown() -> None:
    transcript = read("design-app/transcript-detail.jsx")

    assert (
        'copyFromEndpoint("/transcripts/" + t.id + "/summary.md", "all", t.summary_md)'
        in transcript
    )
    assert (
        'copyFromEndpoint("/transcripts/" + t.id + "/summary.md", "summary", t.summary_md || "")'
        in transcript
    )
    assert 'const path = "/transcripts/" + t.id + "/" + kind + ".md"' in transcript


def test_production_sources_do_not_use_browser_native_dialogs_or_old_globals() -> None:
    source = production_sources()
    forbidden_dialog = re.compile(r"\b(?:window\.)?(alert|confirm|prompt)\s*\(")

    assert forbidden_dialog.search(source) is None
    assert "Object.assign(window" not in source
    assert "Math.random" not in source
    assert 'new Date("2026-05-16T09:45:32Z")' not in source
    assert "DesignSystemPlayground" not in source
    assert "library-hero" not in source
    assert "library-submit" not in source
