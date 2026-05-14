"""go.oklabs.uk shortlinks via the Chhoto shortener (on Edgebox).

Ported from finalize_scribe_job.py. In the scribe architecture these point at
scribe's own web-UI transcript pages, not Obsidian (scribe is Obsidian-agnostic).
The Chhoto endpoint + API key are env-driven (scribe.config), never hardcoded.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from scribe.config import settings


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not chase the shortlink target -- we only verify the shortlink itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _resolves(url: str, timeout: int = 10) -> bool:
    """True if the shortlink itself answers -- a 2xx or any 3xx redirect.

    Crucially this does NOT follow the redirect: the long-link target may be a
    private repo or a not-yet-up page, and that is irrelevant to whether the
    shortlink was created.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 400
    except Exception:
        return False


def make_shortlink(target_url: str, *, verify: bool = True) -> str | None:
    """Create a go.oklabs.uk shortlink for `target_url`. Returns None on failure.

    `verify` does a follow-up GET to confirm the shortlink resolves; callers that
    shorten a not-yet-reachable URL (e.g. a page on this very service before it is
    up) can pass verify=False.
    """
    if not settings.shortlink_api_url or not settings.shortlink_api_key:
        return None
    payload = json.dumps({"longlink": target_url, "expiry_delay": 604800}).encode("utf-8")
    request = urllib.request.Request(
        settings.shortlink_api_url,
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": settings.shortlink_api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except Exception:
        return None
    shorturl = str(data.get("shorturl") or "").strip()
    if not shorturl:
        return None
    slug = shorturl.rstrip("/").rsplit("/", 1)[-1].strip()
    if not slug:
        return None
    candidate = f"{settings.shortlink_base.rstrip('/')}/{slug}"
    if verify and not _resolves(candidate):
        return None
    return candidate
