from __future__ import annotations

import gzip
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"
STYLES = SPA_SRC / "styles.css"
MAIN = SPA_SRC / "main.jsx"
STAGED_STYLES = SPA_SRC / "design-source" / "app" / "styles.css"
SPA_TEMPLATE = ROOT / "src" / "scribe" / "web" / "templates" / "spa.html"
LIVE_VISUAL_QA = ROOT / "scripts" / "live-visual-qa.mjs"
PACKAGE_JSON = ROOT / "web" / "spa" / "package.json"
INDEX_HTML = ROOT / "web" / "spa" / "index.html"

TOKENS = (
    "--bg",
    "--bg-soft",
    "--bg-card",
    "--fg",
    "--fg-soft",
    "--muted",
    "--accent",
    "--accent-fg",
    "--accent-soft",
    "--border",
    "--border-soft",
    "--ok",
    "--warn",
    "--err",
    "--info",
    "--link",
    "--font-display",
    "--font-sans",
    "--font-mono",
    "--radius-sm",
    "--radius",
    "--radius-lg",
    "--shadow-sm",
    "--shadow",
    "--rule",
    "--row-y",
    "--row-gap",
    "--pane-pad",
    "--fs-base",
)

CLASSES = (
    ".app",
    ".topbar",
    ".sidebar",
    ".nav-item",
    ".btn",
    ".btn.primary",
    ".btn.ghost",
    ".iconbtn",
    ".chip",
    ".chip.ok",
    ".chip.warn",
    ".chip.err",
    ".chip.info",
    ".chip.run",
    ".tag",
    ".kbd",
    ".spinner",
    ".live-dot",
    ".bar-track",
    ".toggle",
    ".seg",
    ".cmdk-overlay",
    ".cmdk-modal",
    ".pane",
    ".pane-narrow",
    ".pane-header",
    ".pane-h1",
    ".pane-sub",
    ".section-label",
    ".metric",
    ".metric-grid",
    ".lib-toolbar",
    ".lib-table",
    ".lib-feed",
    ".feed-item",
    ".feed-title",
    ".feed-excerpt",
    ".lib-cards",
    ".card",
    ".detail-h1",
    ".detail-meta",
    ".detail-tags",
    ".transcript-body",
    ".prose",
    ".pipeline",
    ".stage",
    ".stage.active",
    ".stage.done",
    ".stage.pending",
    ".stage.failed",
    ".progressbar",
    ".failure-row",
    ".err-title",
    ".err-msg",
    ".err-meta",
    ".spark",
    ".settings-group",
    ".settings-row",
    ".share-sheet",
    ".users-table",
    ".danger-zone",
    ".empty",
    ".row-label",
    ".row-control",
    ".hint",
    ".tnum",
    ".muted",
    ".mono",
)


def test_spa_css_is_imported_once() -> None:
    source = MAIN.read_text(encoding="utf-8")

    assert source.count('import "./styles.css";') == 1


def test_production_styles_start_from_design_export_stylesheet() -> None:
    css = STYLES.read_text(encoding="utf-8")
    staged = STAGED_STYLES.read_text(encoding="utf-8")

    assert css.startswith(staged)
    assert ".tweaks-panel" not in css
    assert "Production glue states" in css


def test_design_tokens_and_default_variant_matrix_are_present() -> None:
    css = STYLES.read_text(encoding="utf-8")
    index = INDEX_HTML.read_text(encoding="utf-8")
    tweaks = (SPA_SRC / "hooks" / "useTweaks.ts").read_text(encoding="utf-8")

    for token in TOKENS:
        assert f"{token}:" in css
    for selector in (
        '[data-variant="paper"]',
        '[data-variant="terminal"]',
        '[data-variant="console"]',
        '[data-variant="field"]',
        '[data-theme="dark"]',
        '[data-density="compact"]',
        '[data-density="cozy"]',
        '[data-density="comfy"]',
    ):
        assert selector in css
    assert 'data-variant="field"' in index
    assert 'data-theme="light"' in index
    assert 'data-density="compact"' in index
    assert 'data-library-layout="feed"' in index
    assert 'variant: "field"' in tweaks
    assert 'theme: "light"' in tweaks
    assert 'density: "compact"' in tweaks
    assert 'libraryLayout: "feed"' in tweaks


def test_variant_tokens_match_claude_design_export_values() -> None:
    css = STYLES.read_text(encoding="utf-8")

    def block(selector: str) -> str:
        match = re.search(rf"{re.escape(selector)}\s*\{{", css)
        assert match is not None
        start = match.start()
        return css[start : css.index("}", start)]

    assert "--bg-card: #fbf9f3" in block('[data-variant="paper"]')
    assert "--fg: #1c1a16" in block('[data-variant="paper"]')
    assert "--accent: #b15233" in block('[data-variant="paper"]')
    assert "--accent: #7dd87d" in block('[data-variant="terminal"]')
    assert "--bg: #ffffff" in block('[data-variant="console"]')
    assert "--bg: #eceef2" in block('[data-variant="field"]')
    assert "--accent: #657153" in block('[data-variant="field"]')


def test_shared_design_classes_are_present_without_old_ds_playground_primitives() -> None:
    css = STYLES.read_text(encoding="utf-8")

    for class_name in CLASSES:
        assert class_name in css
    for old_class in (".ds-button", ".ds-icon-button", ".ds-card", ".ds-table", ".metric-card"):
        assert old_class not in css
    assert not (SPA_SRC / "DesignSystemPlayground.tsx").exists()


def test_template_and_visual_qa_script_still_cover_routes_and_variants() -> None:
    script = LIVE_VISUAL_QA.read_text(encoding="utf-8")
    package = PACKAGE_JSON.read_text(encoding="utf-8")
    template = SPA_TEMPLATE.read_text(encoding="utf-8")

    assert '"visual:qa": "bun ../../scripts/live-visual-qa.mjs"' in package
    assert '<div id="root"></div>' in template
    assert "SCRIBE_VISUAL_QA_BASE_URL" in script
    for route in (
        "#/library",
        "#/queue",
        "#/ops",
        "#/settings",
        "#/transcript/",
        "#/jobs/",
    ):
        assert route in script
    assert 'const VARIANTS = ["paper", "terminal", "console", "field"]' in script
    assert 'const THEMES = ["light", "dark"]' in script
    assert 'const DENSITIES = ["compact", "cozy", "comfy"]' in script
    assert 'const LIBRARY_LAYOUTS = ["table", "feed", "cards"]' in script
    assert "tweaksPanelAbsent" in script
    assert "commandPaletteMismatch" in script
    assert "Page.captureScreenshot" in script


def test_live_visual_qa_contract_covers_required_runtime_surface() -> None:
    script = LIVE_VISUAL_QA.read_text(encoding="utf-8")

    assert '{ name: "desktop", width: 1440, height: 1000, mobile: false }' in script
    assert '{ name: "mobile-compact", width: 360, height: 800, mobile: true }' in script
    assert '{ name: "mobile", width: 390, height: 900, mobile: true }' in script
    assert '{ name: "mobile-large", width: 430, height: 932, mobile: true }' in script
    for key in (
        'key: "library"',
        'key: "queue"',
        'key: "ops"',
        'key: "settings"',
        'key: `transcript-${id}`',
        'key: `job-${id}`',
        'key: "command-palette"',
    ):
        assert key in script

    assert 'const VARIANTS = ["paper", "terminal", "console", "field"]' in script
    assert 'const THEMES = ["light", "dark"]' in script
    assert 'const DENSITIES = ["compact", "cozy", "comfy"]' in script
    assert 'const LIBRARY_LAYOUTS = ["table", "feed", "cards"]' in script
    assert "variant matrix: ${manifest.variantMatrix.length} combinations" in script
    assert "variantMatrixCountMismatch" in script
    assert "VARIANTS.length * THEMES.length * DENSITIES.length * LIBRARY_LAYOUTS.length" in script
    assert "expected:\n" in script
    assert ".tweaks-panel" in script
    assert "realContent" in script
    assert "hasTitle" in script
    assert "hasBody" in script
    assert 'route.state.dataset.variant !== "field"' in script
    assert 'route.state.dataset.theme !== "light"' in script
    assert 'route.state.dataset.density !== "compact"' in script
    assert 'route.state.dataset.libraryLayout !== "feed"' in script


def test_source_css_gzip_size_budget() -> None:
    css = STYLES.read_bytes()

    assert len(gzip.compress(css)) <= 30_000


def test_mobile_capture_sheet_ships_real_submit_no_fabricated_metadata() -> None:
    """Wave 2f / Issue #281 — mobile CaptureSheet structure guards."""

    def strip_comments(source: str) -> str:
        # Strip // line comments and /* ... */ block comments so the guards
        # never trip on the source mapping / "what NOT to ship" prose.
        without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        return re.sub(r"//[^\n]*", "", without_block)

    capture_raw = (
        SPA_SRC / "design-app" / "mobile" / "CaptureSheet.jsx"
    ).read_text(encoding="utf-8")
    capture = strip_comments(capture_raw)
    api_jobs = (SPA_SRC / "design-app" / "api-jobs.js").read_text(encoding="utf-8")
    palette = (SPA_SRC / "design-app" / "command-palette.jsx").read_text(
        encoding="utf-8"
    )
    main_jsx = MAIN.read_text(encoding="utf-8")

    # CaptureSheet renders the design-source recipe classes (url-field,
    # detected, opt-row alternatives, bigbtn) and never invents metadata.
    assert 'className="url-field"' in capture
    assert 'className="detected"' in capture
    assert 'className="bigbtn"' in capture
    assert 'className="s-cancel"' in capture
    assert 'className="s-done"' in capture
    assert 'className="grabber"' in capture

    # No fabricated channel/title/duration/cost from the design prototype.
    for forbidden in ("Linus Torvalds", "Rich Hickey", "Bryan Cantrill", "est. $0.04"):
        assert forbidden not in capture, (
            f"CaptureSheet must not ship fabricated metadata: {forbidden}"
        )

    # No window.alert/confirm/prompt anywhere in the capture flow.
    for primitive in ("window.alert", "window.confirm", "window.prompt"):
        assert primitive not in capture
        assert primitive not in api_jobs
        assert primitive not in main_jsx

    # Shared submitJob helper is the single submit path: it must POST /jobs
    # with body {url, source: "manual"} via auth.protectedFetch.
    assert 'auth.protectedFetch("/jobs"' in api_jobs
    assert '"manual"' in api_jobs
    assert "source: \"manual\"" in api_jobs

    # Both CmdK and the mobile shell consume the shared helper.
    assert 'from "./api-jobs.js"' in palette
    assert "submitJob(auth, " in palette
    assert 'from "./design-app/api-jobs.js"' in main_jsx
    assert "submitJob(auth, " in main_jsx

    # CaptureSheet calls the host-provided onSubmit with the parsed URL —
    # no inline fetch from the component.
    assert "auth.protectedFetch" not in capture
    assert "onSubmit?.(parsed.url)" in capture

    # Optional toggles (Summarize / Notify / Prompt) are NOT rendered in v1
    # — POST /jobs does not yet support those flags.
    assert 'data-act="sum"' not in capture
    assert 'data-act="notify"' not in capture

    # Main wires success → navigate to #/jobs/:id + .toast "Added to queue".
    assert 'navigate({ page: "job"' in main_jsx
    assert '"Added to queue"' in main_jsx
