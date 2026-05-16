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
    "--font-mono",
    "--radius",
    "--radius-lg",
    "--rule",
    "--row-y",
    "--row-gap",
    "--pane-pad",
    "--fs-base",
)

CLASSES = (
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
    ".cmdk-",
    ".pane",
    ".pane-narrow",
    ".pane-header",
    ".pane-h1",
    ".pane-sub",
    ".section-label",
    ".metric",
    ".metric-grid",
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


def test_design_tokens_exist_for_all_variant_theme_combos() -> None:
    css = STYLES.read_text(encoding="utf-8")

    for variant in ("paper", "terminal", "console"):
        for theme in ("light", "dark"):
            assert f'[data-variant="{variant}"][data-theme="{theme}"]' in css
            start = css.index(f'[data-variant="{variant}"][data-theme="{theme}"]')
            end = css.index("}", start)
            block = css[start:end]
            for token in TOKENS:
                assert f"{token}:" in block


def test_density_modulates_only_spacing_and_base_size() -> None:
    css = STYLES.read_text(encoding="utf-8")

    for density in ("compact", "cozy", "comfy"):
        start = css.rindex(f'[data-density="{density}"]')
        end = css.index("}", start)
        block = css[start:end]
        assert "--row-y:" in block
        assert "--row-gap:" in block
        assert "--pane-pad:" in block
        assert "--fs-base:" in block


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
    for family in ("Newsreader", "Inter", "JetBrains+Mono", "Geist", "Geist+Mono"):
        assert family in template

    client = TestClient(app)
    response = client.get("/__spa__/__playground__")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_source_css_gzip_size_budget() -> None:
    css = STYLES.read_bytes()

    assert len(gzip.compress(css)) <= 30_000
