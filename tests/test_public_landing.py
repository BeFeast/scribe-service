"""The signed-out home page must describe the product, not just gate it.

Google's OAuth brand review rejected the app because the home page "does not
explain the purpose of your app" — a reviewer landing on scribe.oklabs.uk saw
only a sign-in card. These assertions pin the public copy that answers it.
"""
from __future__ import annotations

from pathlib import Path

SPA_SRC = Path(__file__).resolve().parents[1] / "web" / "spa" / "src"


def _read(rel: str) -> str:
    return (SPA_SRC / rel).read_text(encoding="utf-8")


def test_landing_states_product_name_and_purpose() -> None:
    source = _read("components/Loaders.tsx")

    assert 'className="auth-gate-about"' in source
    # The name must match the Google OAuth consent-screen app name exactly.
    assert "<h2 id=\"about-scribe\">Scribe</h2>" in source
    assert "transcript" in source and "summary" in source


def test_landing_explains_google_sign_in_usage_and_links_policies() -> None:
    """Brand review also checks that the page says how the Google account is
    used, and that privacy/terms are reachable from it."""
    source = _read("components/Loaders.tsx")

    assert "Signing in with Google" in source
    assert "never posts anything" in source
    assert 'href="/privacy"' in source
    assert 'href="/terms"' in source


def test_shell_landing_matches_the_in_app_copy() -> None:
    """The server-rendered fallback in spa.html and the React section must tell
    the same story — Google's reviewer reads the raw document, users read the
    rendered one, and a drift between them is how the app failed review twice."""
    raw_shell = (
        Path(__file__).resolve().parents[1]
        / "src" / "scribe" / "web" / "templates" / "spa.html"
    ).read_text(encoding="utf-8")
    # Both files wrap prose across lines, so compare on collapsed whitespace.
    shell = " ".join(raw_shell.split())
    react = " ".join(_read("components/Loaders.tsx").split())

    for phrase in (
        "searchable transcript",
        "Signing in with Google is used only to identify you",
        "never posts anything",
        "invite-only",
    ):
        assert phrase in shell, phrase
        assert phrase in react, phrase

    # Product name in the raw document must equal the consent-screen app name.
    assert "<h1>Scribe</h1>" in raw_shell
    assert 'class="shell-landing"' in raw_shell
