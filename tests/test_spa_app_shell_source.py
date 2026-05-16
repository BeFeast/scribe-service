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
    ):
        assert (SPA_SRC / path).is_file()


def test_app_mounts_shell_and_placeholder_router() -> None:
    source = read("main.tsx")

    assert "TopBar" in source
    assert "Sidebar" in source
    assert "TweaksPanel" in source
    assert "useRoute" in source
    assert "pages coming online — see issue #27" in source
    assert 'route.page === "library"' in source


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
    assert 'variant: "paper"' in source
    assert 'theme: "light"' in source
    assert 'density: "cozy"' in source
    assert 'libraryLayout: "feed"' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert "document.documentElement" in source
    assert "dataset.theme = tweaks.theme" in source


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

    assert 'export type RoutePage = "library" | "transcript" | "queue" | "job" | "ops" | "settings";' in source
    assert "window.location.hash" in source
    assert "window.addEventListener(\"hashchange\"" in source
    assert "type RouteAction" in source
