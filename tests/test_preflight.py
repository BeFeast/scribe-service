"""Offline yt-dlp preflight matcher + GET /preflight route (#339).

The matcher tests are pure (no DB, no network) and assert the corrected
single-media discriminator: a dedicated extractor is necessary but NOT
sufficient — the YouTube *home* page matches the dedicated ``YoutubeRecommended``
extractor yet must be classified as a non-single-media container. The route
tests run against the in-process app via trusted-LAN auth (TestClient), with
the DB session dependency overridden so no Postgres is required.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.api.preflight import PreflightResult, match_url
from scribe.main import app

WATCH = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SHORT = "https://youtu.be/dQw4w9WgXcQ"
HOME = "https://www.youtube.com/"
PLAYLIST = "https://www.youtube.com/playlist?list=PL1234567890"
CHANNEL = "https://www.youtube.com/@channel"
SEARCH = "https://www.youtube.com/results?search_query=test"
VIMEO = "https://vimeo.com/76979871"
RANDOM = "https://example.com/some-random-article"


# --------------------------------------------------------------------------
# Pure matcher — single-media discrimination
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", [WATCH, SHORT, VIMEO])
def test_single_media_urls_are_auto_submittable(url: str) -> None:
    result = match_url(url)
    assert result.supported is True
    assert result.single_media is True
    assert result.return_type == "video"
    assert result.extractor
    assert result.generic_only is False


def test_youtube_home_matches_dedicated_extractor_but_is_not_single_media() -> None:
    """The crux of #339: a dedicated extractor (YoutubeRecommended) claims the
    home page, so karaoke's bare ``supported`` verdict would auto-submit it.
    The corrected verdict demotes it via ``return_type``/``single_media``."""
    result = match_url(HOME)
    assert result.supported is True  # YoutubeRecommended is a dedicated extractor
    assert result.single_media is False  # ...but it returns a feed, not a video
    assert result.return_type != "video"


@pytest.mark.parametrize("url", [PLAYLIST, CHANNEL, SEARCH])
def test_container_urls_are_supported_but_not_single_media(url: str) -> None:
    result = match_url(url)
    assert result.supported is True
    assert result.single_media is False
    assert result.return_type != "video"


def test_unsupported_url_is_generic_only_not_single_media() -> None:
    result = match_url(RANDOM)
    assert result.supported is False
    assert result.single_media is False
    assert result.generic_only is True


@pytest.mark.parametrize("url", ["", "not a url", "ftp://example.com/x", "chrome://extensions"])
def test_non_http_urls_short_circuit_to_invalid(url: str) -> None:
    result = match_url(url)
    assert result == PreflightResult(
        supported=False,
        extractor=None,
        return_type=None,
        single_media=False,
        generic_only=False,
    )


# --------------------------------------------------------------------------
# GET /preflight route — trusted-LAN, no DB
# --------------------------------------------------------------------------


@pytest.fixture()
def client():
    # Trusted-LAN auth (TestClient host) covers the gate; the route never reads
    # the DB, so the session dependency is overridden to a no-op.
    app.dependency_overrides[routes_module.get_session] = lambda: None
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)


def test_preflight_route_marks_watch_page_single_media(client: TestClient) -> None:
    resp = client.get("/preflight", params={"url": WATCH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["single_media"] is True
    assert body["return_type"] == "video"
    assert set(body) == {"supported", "extractor", "return_type", "single_media", "generic_only"}


def test_preflight_route_does_not_mark_youtube_home_single_media(client: TestClient) -> None:
    resp = client.get("/preflight", params={"url": HOME})
    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["single_media"] is False


def test_preflight_route_empty_url_is_a_normal_unsupported_verdict(client: TestClient) -> None:
    resp = client.get("/preflight")
    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is False
    assert body["single_media"] is False
    assert body["generic_only"] is False
