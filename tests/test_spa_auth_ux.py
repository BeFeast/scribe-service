"""Source-level tests for the reset SPA auth UX.

The reset keeps auth as backend glue while replacing old visual pages/components
with Claude Design-derived modules.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"


def read(relative: str) -> str:
    return (SPA_SRC / relative).read_text(encoding="utf-8")


def production_sources() -> str:
    parts: list[str] = []
    for path in SPA_SRC.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            if "design-source" in path.parts:
                continue
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_use_auth_exposes_clerk_sign_in_with_loop_protection() -> None:
    source = read("hooks/useAuth.tsx")

    assert "redirectToSignIn" in source
    assert "redirectToSignUp" in source
    assert "openSignIn" not in source
    assert "openSignUp" not in source
    assert "sessionStorage" in source
    assert "maybeAutoSignIn" in source
    assert "authRedirectInFlight" in source
    assert "authRequired" in source
    assert "shouldRequireSignIn" in source
    assert "Authentication resources were blocked" in source
    assert "protectedFetch" in source
    assert 'headers.set("Authorization"' in source
    assert "Bearer" in source


def test_design_app_runtime_routes_auth_failures_through_shared_fetch_glue() -> None:
    api = read("design-app/api.jsx")
    main = read("main.jsx")
    library = read("design-app/library.jsx")

    assert "AuthProvider" in main
    assert "useAuth()" in main
    assert "auth={auth}" in main
    assert "auth.protectedFetch" in api
    assert "response.status === 401 || response.status === 403" in api
    assert "auth.maybeAutoSignIn()" in api
    assert "throw new HttpError(response.status, await responseMessage(response))" in api
    assert "isTransientFetchError" in api
    assert "auth.signedIn" in api
    assert "AuthRequiredState" in library
    assert "auth?.authRequired" in library
    assert "Sign in required" in library
    assert "auth.signIn()" in library
    assert "auth.retryAuth()" in library


def test_settings_account_and_token_controls_stay_wired_without_dead_rotate_ui() -> None:
    settings = read("design-app/settings.jsx")
    production = production_sources()

    assert "Manage in Clerk" in settings
    assert "auth.signOut" in settings
    assert "Generate token" in settings
    assert "Copy token" in settings
    assert "/api/auth/extension-token" in settings
    assert "/api/config/rotate-token" not in production
    assert "configured server-side" not in production


def test_shared_auth_helper_covers_401_and_403() -> None:
    source = read("lib/auth.ts")

    assert "isAuthStatus" in source
    assert "401" in source
    assert "403" in source


def test_no_mock_sidebar_or_old_auth_visual_components_remain() -> None:
    source = production_sources()

    assert "[mock]" not in source
    assert "mockTags" not in source
    assert "mockPipeline" not in source
    assert "mock-chip" not in source
    assert "components/Sidebar" not in source
    assert "components/TopBar" not in source
    assert "pages/Library" not in source


def test_spa_sources_do_not_use_browser_native_dialogs() -> None:
    forbidden = re.compile(r"\b(?:window\.)?(alert|confirm|prompt)\s*\(")
    for path in SPA_SRC.rglob("*"):
        if not path.is_file() or path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        if "design-source" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        match = forbidden.search(source)
        assert match is None, f"{path.relative_to(SPA_SRC)} uses browser-native dialog"
