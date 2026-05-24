from __future__ import annotations

import gzip
from pathlib import Path

from fastapi.testclient import TestClient

from scribe.main import app

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"
STYLES = SPA_SRC / "styles.css"
MAIN = SPA_SRC / "main.tsx"
PLAYGROUND = SPA_SRC / "DesignSystemPlayground.tsx"
SPA_TEMPLATE = ROOT / "src" / "scribe" / "web" / "templates" / "spa.html"
LIVE_VISUAL_QA = ROOT / "scripts" / "live-visual-qa.mjs"
PACKAGE_JSON = ROOT / "web" / "spa" / "package.json"

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
    ".btn",
    ".ds-button",
    ".btn.primary",
    ".btn.ghost",
    ".iconbtn",
    ".ds-icon-button",
    ".chip",
    ".chip.ok",
    ".chip.warn",
    ".chip.err",
    ".chip.info",
    ".chip.run",
    ".tag",
    ".kbd",
    ".divider",
    ".rule",
    ".spinner",
    ".live-dot",
    ".bar-track",
    ".toggle",
    ".seg",
    ".cmdk-",
    ".pane",
    ".pane-narrow",
    ".pane-header",
    ".pane-h1",
    ".pane-sub",
    ".section-label",
    ".metric",
    ".metric-card",
    ".metric-grid",
    ".lib-table",
    ".ds-table",
    ".lib-feed",
    ".feed-item",
    ".feed-title",
    ".feed-excerpt",
    ".lib-cards",
    ".card",
    ".ds-card",
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
    ".access-row",
    ".loading-state",
    ".empty-state",
    ".error-state",
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


def test_design_tokens_are_defined_in_root() -> None:
    css = STYLES.read_text(encoding="utf-8")

    selector = '[data-variant="field"][data-theme="light"][data-density="compact"]'
    assert selector in css
    selector_start = css.index(selector)
    selector_block = css[selector_start : css.index("}", selector_start)]
    assert "color-scheme: light" in selector_block

    start = css.index(":root")
    end = css.index("}", start)
    block = css[start:end]
    for token in TOKENS:
        assert f"{token}:" in block
    for value in (
        "#eceef2",
        "#d1d5de",
        "#f5f6f9",
        "#1c2018",
        "#3a4234",
        "#837569",
        "#b7b6c2",
        "#c8ccd3",
        "#657153",
        "#d8dfcd",
        "#a08254",
        "#8a4a3a",
        "#5d7088",
    ):
        assert value in block


def test_density_modulates_only_spacing_and_base_size() -> None:
    css = STYLES.read_text(encoding="utf-8")

    start = css.index(":root")
    end = css.index("}", start)
    block = css[start:end]
    assert "--row-y: 9px" in block
    assert "--row-gap: 8px" in block
    assert "--pane-pad: 24px" in block
    assert "--fs-base: 13px" in block


def test_shared_component_classes_are_present() -> None:
    css = STYLES.read_text(encoding="utf-8")

    for class_name in CLASSES:
        assert class_name in css


def test_playground_route_and_font_links_are_wired() -> None:
    playground = PLAYGROUND.read_text(encoding="utf-8")
    template = SPA_TEMPLATE.read_text(encoding="utf-8")

    assert "DesignSystemPlayground" in MAIN.read_text(encoding="utf-8")
    assert '"/__spa__/__playground__"' in MAIN.read_text(encoding="utf-8")
    assert 'data-variant={variant}' in playground
    assert 'data-theme={theme}' in playground
    assert 'data-density={density}' in playground
    for family in ("Inter", "JetBrains+Mono", "Geist", "Geist+Mono"):
        assert family in template

    client = TestClient(app)
    response = client.get("/__spa__/__playground__")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_source_css_gzip_size_budget() -> None:
    css = STYLES.read_bytes()

    assert len(gzip.compress(css)) <= 30_000


def test_library_submit_status_does_not_shift_toolbar_row() -> None:
    """Regression for #121: the queued/error status must keep the Submit
    URL input aligned with the Search input, while still reserving
    in-flow vertical space so wrapped messages do not overlap the next
    row."""
    css = STYLES.read_text(encoding="utf-8")

    # Top-aligning the actions grid keeps both cells' input baselines
    # locked to the same Y regardless of whether the status row exists.
    start = css.index(".library-actions {")
    block = css[start : css.index("}", start)]
    assert "align-items: start" in block

    # Status must remain in normal flow (spanning the form's columns)
    # so wrapping messages reserve their own vertical space rather
    # than overlapping subsequent rows.
    start = css.index(".library-submit-status {")
    block = css[start : css.index("}", start)]
    assert "grid-column: 1 / -1" in block
    assert "position: absolute" not in block


def test_live_visual_qa_script_covers_required_routes_and_responsive_viewports() -> None:
    script = LIVE_VISUAL_QA.read_text(encoding="utf-8")
    package = PACKAGE_JSON.read_text(encoding="utf-8")

    assert '"visual:qa": "bun ../../scripts/live-visual-qa.mjs"' in package
    assert 'http://10.10.0.13:13120/' in script
    for route in (
        "#/library",
        "#/queue",
        "#/ops",
        "#/settings",
        "#/transcript/${explicitTranscript}",
        "#/jobs/${explicitJob}",
    ):
        assert route in script
    assert "SCRIBE_VISUAL_QA_TRANSCRIPT_ID" in script
    assert "SCRIBE_VISUAL_QA_JOB_ID" in script
    assert '"desktop", width: 1440' in script
    assert '"mobile", width: 390' in script
    assert "Page.captureScreenshot" in script
    assert "Runtime.consoleAPICalled" in script
    assert "Runtime.exceptionThrown" in script
    assert "horizontalOverflow" in script
    assert "commandPaletteMismatch" in script
    assert 'dataset.variant !== "field"' in script
    assert 'dataset.theme !== "light"' in script
    assert 'dataset.density !== "compact"' in script
    assert 'dataset.libraryLayout !== "feed"' in script
    assert "Input.dispatchKeyEvent" in script


def test_responsive_shell_and_route_surfaces_prevent_viewport_overflow() -> None:
    css = STYLES.read_text(encoding="utf-8")

    def rule_block(selector: str) -> str:
        selector_start = css.index(selector)
        block_start = css.index("{", selector_start)
        return css[block_start : css.index("}", block_start)]

    for selector in (
        ".content-pane",
        ".library-results",
        ".library-search",
        ".library-submit label",
        ".inflight-copy",
        ".cmdk-submit .label",
        ".cmdk-result-body",
    ):
        assert "min-width: 0" in rule_block(selector)

    for selector in (".table-wrap", ".access-table-wrap", ".pipeline.compact"):
        assert "overflow-x: auto" in rule_block(selector)

    narrow = css[css.index("@media (max-width: 820px)") :]
    assert ".shell-body" in narrow
    assert "grid-template-columns: 1fr" in narrow
    assert ".sidebar" in narrow
    assert "position: static" in narrow
    assert ".library-actions" in narrow

    mobile = css[css.index("@media (max-width: 560px)") :]
    assert ".topbar-access" in mobile
    assert "width: 100%" in mobile
