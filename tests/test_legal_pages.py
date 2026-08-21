"""Public /privacy and /terms pages (no DB, no auth).

Google refuses to publish an OAuth app whose consent-screen privacy policy and
terms links are missing or unreachable, so these two routes are a hard
dependency of Google sign-in — not decoration.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scribe.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "heading", "marker"),
    [
        ("/privacy", "Privacy", "What we store"),
        ("/terms", "Terms of Service", "Acceptable use"),
    ],
)
def test_legal_page_renders_for_anonymous_visitor(client, path, heading, marker) -> None:
    page = client.get(path)

    assert page.status_code == 200
    assert heading in page.text
    assert marker in page.text
    # Rendered through the shared shell rather than served as a bare fragment.
    assert "<!doctype html>" in page.text.lower()


def test_legal_pages_state_a_revision_date(client) -> None:
    """A consent-screen policy without a revision date reads as unmaintained."""
    assert "Last updated" in client.get("/privacy").text
    assert "Last updated" in client.get("/terms").text
