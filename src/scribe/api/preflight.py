"""Offline yt-dlp extractor matching for URL-support checks (#339).

Answers "would the deployed yt-dlp treat this URL as a single playable video?"
entirely in-process: no network, no subprocess, no ``yt_dlp.YoutubeDL``
instance. The verdict is version-true — it runs against the same installed
``yt_dlp`` the pipeline downloads with (``src/scribe/pipeline/downloader.py``).

Ported from karaoke's preflight (BeFeast/karaoke#180), **corrected** for the
bug in #339: karaoke marks a URL ``supported`` whenever a dedicated
(non-``Generic``) extractor matches, but that still auto-submits the YouTube
*home* page — its dedicated ``YoutubeRecommended`` extractor matches yet
returns a *feed*, not a video. The fix is to also read the matched extractor's
``_RETURN_TYPE`` (a real yt-dlp ``InfoExtractor`` attribute) and only call a
URL ``single_media`` when that type is exactly ``"video"``. Containers come
back as ``any`` / ``playlist`` / ``None`` and must be confirmed by the caller,
never auto-submitted.

The extractor class list is static for a given yt-dlp version, so it is built
once at import. yt-dlp ships lazy extractor classes whose URL regex compiles
on the first ``suitable()`` call — a one-time cost across the full list — so
the warm-up sweep below moves that cost from the first request to process
start.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from yt_dlp.extractor import gen_extractor_classes

_EXTRACTORS = tuple(gen_extractor_classes())

# yt-dlp's InfoExtractor._RETURN_TYPE value for a single playable item.
# Containers report "any" / "playlist"; feed-style extractors report None.
_SINGLE_MEDIA_RETURN_TYPE = "video"


@dataclass(frozen=True)
class PreflightResult:
    """Verdict of matching a URL against yt-dlp's extractors.

    * ``supported`` — a dedicated (non-``Generic``) extractor claims the URL.
    * ``extractor`` — that extractor's ``IE_NAME``, else ``None``.
    * ``return_type`` — the matched extractor's ``_RETURN_TYPE`` (``"video"`` /
      ``"playlist"`` / ``"any"`` / ``None``), else ``None``.
    * ``single_media`` — the only auto-submit signal: ``supported`` **and**
      ``return_type == "video"``. A container (playlist/feed/channel/search)
      is ``supported`` but **not** ``single_media``.
    * ``generic_only`` — the URL is syntactically valid http(s) but only the
      catch-all ``Generic`` extractor matched (yt-dlp *might* scrape
      something, with no guarantee it is a video).
    """

    supported: bool
    extractor: str | None
    return_type: str | None
    single_media: bool
    generic_only: bool


_INVALID_URL = PreflightResult(
    supported=False,
    extractor=None,
    return_type=None,
    single_media=False,
    generic_only=False,
)


def _is_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def match_url(url: str) -> PreflightResult:
    """Match ``url`` against the installed yt-dlp's extractors, offline.

    Invalid / non-http(s) input short-circuits to an "unsupported" verdict —
    yt-dlp's ``Generic`` extractor pattern-matches nearly any string, so it
    must not be consulted before the URL itself is known to be plausible.
    """
    if not _is_http_url(url):
        return _INVALID_URL
    generic_matched = False
    for ie in _EXTRACTORS:
        if not ie.suitable(url):
            continue
        if ie.ie_key() == "Generic":
            generic_matched = True
            continue
        return_type = getattr(ie, "_RETURN_TYPE", None)
        return PreflightResult(
            supported=True,
            extractor=ie.IE_NAME,
            return_type=return_type,
            single_media=return_type == _SINGLE_MEDIA_RETURN_TYPE,
            generic_only=False,
        )
    return PreflightResult(
        supported=False,
        extractor=None,
        return_type=None,
        single_media=False,
        generic_only=generic_matched,
    )


# Warm-up sweep: nothing dedicated matches this URL, so every extractor's lazy
# ``suitable()`` regex gets compiled exactly once, here.
match_url("https://preflight-warmup.invalid/")
