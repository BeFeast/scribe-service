from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"


def read(path: str) -> str:
    return (SPA_SRC / path).read_text(encoding="utf-8")


def test_app_shell_files_exist() -> None:
    for path in (
        "components/TopBar.tsx",
        "components/Sidebar.tsx",
        "components/TweaksPanel.tsx",
        "constants.ts",
        "hooks/useRoute.ts",
        "hooks/useTweaks.ts",
        "pages/Library.tsx",
        "pages/Transcript.tsx",
    ):
        assert (SPA_SRC / path).is_file()


def test_app_mounts_shell_and_placeholder_router() -> None:
    source = read("main.tsx")

    assert "TopBar" in source
    assert "Sidebar" in source
    assert "TweaksPanel" not in source
    assert "useRoute" in source
    assert "Library" in source
    assert "layout={tweaks.libraryLayout}" in source
    assert 'route.page === "library"' in source
    assert 'route.page === "transcript"' in source
    assert "<Transcript" in source


def test_transcript_page_fetches_json_and_renders_markdown_locally() -> None:
    source = read("pages/Transcript.tsx")

    assert 'fetch(`/transcripts/${id}`' in source
    assert 'fetch(`/transcripts/${id}/resummarize`' in source
    assert 'headers: { Accept: "application/json" }' in source
    assert "function parseMd" in source
    assert "function inline" in source
    assert "navigator.clipboard.writeText" in source
    assert "Run summarizer" in source


def test_library_page_fetches_api_and_supports_layouts() -> None:
    source = read("pages/Library.tsx")

    assert "function InFlightStrip" in source
    assert "function InFlightRow" in source
    assert "function LibTable" in source
    assert "function LibFeed" in source
    assert "function LibCards" in source
    assert "buildLibraryUrl(debouncedQuery, selectedTag, libraryPageSize, offset)" in source
    assert '["tag", tag ?? ""]' in source
    assert '["limit", String(limit)]' in source
    assert '["offset", String(offset)]' in source
    assert "setRetryTick((value) => value + 1)" in source
    assert "Previous" in source
    assert "Next" in source
    assert "window.setTimeout(() => setDebouncedQuery(query), 200)" in source
    assert 'fetch("/api/jobs/active"' in source
    assert "document.hidden" in source
    assert "hasNonTerminalJob(body.jobs) ? 5000 : 30000" in source
    assert "CMDK_OPEN_EVENT" in source
    assert "chip warn" in source
    assert "partial" in source
    assert 'layout === "table"' in source
    assert 'layout === "feed"' in source
    assert 'layout === "cards"' in source


def test_cmdk_custom_event_is_wired_without_window_globals() -> None:
    constants = read("constants.ts")
    main = read("main.tsx")
    topbar = read("components/TopBar.tsx")

    assert 'export const CMDK_OPEN_EVENT = "scribe:cmdk-open";' in constants
    assert "CMDK_OPEN_EVENT" in main
    assert "CMDK_OPEN_EVENT" in topbar
    assert "document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT))" in main
    assert "document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT))" in topbar
    assert "window.scribe" not in main + topbar


def test_tweaks_defaults_persist_and_apply_to_html_dataset() -> None:
    source = read("hooks/useTweaks.ts")

    assert 'const STORAGE_KEY = "scribe.tweaks";' in source
    assert 'variant: "terminal"' in source
    assert 'theme: "light"' in source
    assert 'density: "cozy"' in source
    assert 'libraryLayout: "feed"' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert "parsed.variant" not in source
    assert "parsed.density" not in source
    assert "parsed.libraryLayout" not in source
    assert 'type: "variant"' not in source
    assert 'type: "density"' not in source
    assert 'type: "libraryLayout"' not in source
    assert "document.documentElement" in source
    assert "dataset.variant = tweaks.variant" in source
    assert "dataset.theme = tweaks.theme" in source
    assert "dataset.density = tweaks.density" in source
    assert "dataset.libraryLayout = tweaks.libraryLayout" in source


def test_sidebar_has_api_fetch_and_marked_mock_fallback() -> None:
    source = read("components/Sidebar.tsx")

    assert "TODO: replace with /api/library + /api/ops" in source
    assert 'fetch("/api/library?limit=100"' in source
    assert 'fetch("/api/ops"' in source
    assert "[mock]" in source
    assert "mockTags" in source
    assert "mockPipeline" in source


def test_route_hook_uses_typed_hash_routes() -> None:
    source = read("hooks/useRoute.ts")

    assert "export type RoutePage =" in source
    for page in ("library", "transcript", "queue", "job", "ops", "settings"):
        assert f'| "{page}"' in source
    assert "window.location.hash" in source
    assert "window.addEventListener(\"hashchange\"" in source
    assert "type RouteAction" in source
    assert 'case "jobs":' in source
    assert 'parts[0] = "jobs";' in source
