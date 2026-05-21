"""Display helpers for source-provider links."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceLink:
    label: str
    url: str


def source_link_for_url(url: str | None) -> SourceLink | None:
    if not url:
        return None
    raw_url = url.strip()
    if not raw_url:
        return None

    host = (urlparse(raw_url).hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]

    if host in {"x.com", "twitter.com"}:
        label = "Twitter/X"
    elif host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        label = "YouTube"
    else:
        label = host
    return SourceLink(label=label, url=raw_url)
