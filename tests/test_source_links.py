from __future__ import annotations

from scribe.source_links import source_link_for_url


def test_source_link_labels_known_providers() -> None:
    twitter = source_link_for_url("https://x.com/example/status/123")
    assert twitter is not None
    assert twitter.label == "Twitter/X"
    assert twitter.url == "https://x.com/example/status/123"

    youtube = source_link_for_url("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert youtube is not None
    assert youtube.label == "YouTube"


def test_source_link_uses_hostname_for_unknown_provider() -> None:
    source = source_link_for_url("https://video.example.test/watch/abc")
    assert source is not None
    assert source.label == "video.example.test"
    assert source.url == "https://video.example.test/watch/abc"
