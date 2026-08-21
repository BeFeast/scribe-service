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
