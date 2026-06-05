"""In-process cookie jar used to hand per-job cookies from API to worker."""
from __future__ import annotations

from scribe.api import cookie_jar


def test_stash_take_roundtrip():
    cookie_jar.stash(101, "blob-A")
    assert cookie_jar.take(101) == "blob-A"
    # Take pops — second take is None.
    assert cookie_jar.take(101) is None


def test_take_missing_returns_none():
    assert cookie_jar.take(99999) is None


def test_discard_is_idempotent():
    cookie_jar.stash(202, "blob-B")
    cookie_jar.discard(202)
    cookie_jar.discard(202)
    assert cookie_jar.take(202) is None
