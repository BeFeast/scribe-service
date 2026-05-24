"""Source-level tests for the SPA auth UX (issue #129).

These string-based assertions over the TypeScript sources catch the regressions
described in the issue without a Node-side test harness: raw `Library request
failed: 401` copy, `[mock]` sidebar fallbacks shown to signed-out users, and a
missing Clerk sign-in path for 401/403 responses.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"


def read(relative: str) -> str:
    return (SPA_SRC / relative).read_text(encoding="utf-8")


def test_use_auth_exposes_clerk_sign_in_with_loop_protection() -> None:
    source = read("hooks/useAuth.tsx")

    # Clerk is the production sign-in path; issue #131 keeps it redirect-only
    # so extensions cannot close competing popups/modals.
    assert "redirectToSignIn" in source
    assert "redirectToSignUp" in source
    assert "openSignIn" not in source
    assert "openSignUp" not in source
    # Once-per-session guard so a rejected/failed sign-in cannot spam modals.
    assert "sessionStorage" in source
    assert "maybeAutoSignIn" in source
    assert "authRedirectInFlight" in source
    assert "Authentication resources were blocked" in source


def test_library_handles_auth_required_without_raw_401_copy() -> None:
    source = read("pages/Library.tsx")

    # The raw "Library request failed: <status>" string the issue calls out
    # must not be thrown back at the user as the primary error UX.
    assert "Library request failed" not in source
    # The misleading "Library unavailable" outage banner is replaced by a
    # distinct auth-required state and a real service-unavailable state.
    assert "Library unavailable" not in source
    assert "Sign in required" in source
    assert "Service temporarily unavailable" in source

    # 401/403 routes through the typed auth state and shows a stable Clerk CTA.
    assert "isAuthStatus" in source
    assert 'kind: "auth"' in source
    assert "auth.maybeAutoSignIn()" in source
    assert "auth.signIn" in source
    assert "auth.signUp" in source

    # Submit URL / search must not look usable in the signed-out protected
    # state — the inputs are disabled until the user signs in.
    assert "disabled={authRequired}" in source


def test_shared_auth_helper_covers_401_and_403() -> None:
    source = read("lib/auth.ts")

    # Single source of truth for which HTTP statuses gate the auth UX.
    assert "isAuthStatus" in source
    assert "401" in source
    assert "403" in source


def test_sidebar_renders_no_mock_data_when_auth_required() -> None:
    source = read("components/Sidebar.tsx")

    # All traces of the [mock] fallback are gone.
    assert "[mock]" not in source
    assert "mockTags" not in source
    assert "mockPipeline" not in source
    assert "mock-chip" not in source

    # Auth-required is a discriminated UI state, not a thrown error.
    assert "auth-required" in source
    assert "isAuthStatus" in source

    # Sign in CTA wires into the shared useAuth hook and is disabled until
    # the Clerk browser runtime is ready, matching the Library page.
    assert "auth.signIn" in source
    assert "auth.clerkReady" in source


def test_top_bar_signin_button_remains_wired_for_clerk() -> None:
    source = read("components/TopBar.tsx")

    # The Read-only banner already shipped via #106; #129 keeps the Sign in
    # button as the explicit user-action entry point into Clerk.
    assert "auth.signIn" in source
    assert "auth.authRedirectInFlight" in source
    assert "Sign in" in source


def test_spa_sources_do_not_use_browser_native_dialogs() -> None:
    forbidden = re.compile(r"\b(?:window\.)?(alert|confirm|prompt)\s*\(")
    targets = (
        "main.tsx",
        "components/Sidebar.tsx",
        "components/TopBar.tsx",
        "components/CommandPalette.tsx",
        "components/ConfirmDialog.tsx",
        "components/FailureRow.tsx",
        "components/JobCard.tsx",
        "components/Markdown.tsx",
        "components/PipelineDiagram.tsx",
        "components/StatusChip.tsx",
        "components/LogTail.tsx",
        "hooks/useAuth.tsx",
        "hooks/useEventSource.ts",
        "hooks/usePoll.ts",
        "hooks/useRoute.ts",
        "hooks/useTweaks.ts",
        "pages/JobDetail.tsx",
        "pages/Library.tsx",
        "pages/Ops.tsx",
        "pages/Queue.tsx",
        "pages/Settings.tsx",
        "pages/Transcript.tsx",
    )
    for relative in targets:
        source = read(relative)
        match = forbidden.search(source)
        assert match is None, f"{relative} uses browser-native dialog: {match.group(0) if match else ''}"
