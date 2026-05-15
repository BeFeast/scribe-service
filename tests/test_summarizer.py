"""Tests for scribe.pipeline.summarizer — token detection + lock acquisition.

Focused on the resilience surface that PR #1 introduced: signature-matching
against codex stderr, and the lock_timeout / LockTimeoutError contract on
`_acquire_codex_lock`. The actual `summarize()` call shells out to codex so
it is not exercised here — covered by live smoke tests on the deployed
container."""
from __future__ import annotations

import fcntl
import os
import threading
import time

import pytest

from scribe.pipeline import summarizer

# ---------- _is_token_revoked --------------------------------------------------

REVOKED_SAMPLES = [
    'invalid_request_error", "code": "refresh_token_reused"',
    "Encountered invalidated oauth token for user, failing request",
    "Your access token could not be refreshed because your refresh token was already used.",
    'status_code=401, message="token_revoked"',
    "ERROR: Please log out and sign in again",
]


@pytest.mark.parametrize("stderr", REVOKED_SAMPLES)
def test_is_token_revoked_detects_signatures(stderr: str) -> None:
    assert summarizer._is_token_revoked(stderr) is True


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "rate limit exceeded",
        "model is not supported when using Codex with a ChatGPT account",
        "tools cannot be used with reasoning.effort 'minimal'",
        "Traceback (most recent call last):\n  File ...\nValueError: x",
    ],
)
def test_is_token_revoked_false_for_other_errors(stderr: str) -> None:
    assert summarizer._is_token_revoked(stderr) is False


# ---------- _acquire_codex_lock -----------------------------------------------

def test_acquire_codex_lock_no_timeout_succeeds_when_free(tmp_path):
    """With no contention, unbounded LOCK_EX returns immediately."""
    fd = os.open(str(tmp_path / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        summarizer._acquire_codex_lock(fd, timeout=None)
        # we hold it now; cleanup via LOCK_UN
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_acquire_codex_lock_timeout_succeeds_when_free(tmp_path):
    """Even with a finite timeout, an uncontended lock acquires immediately."""
    fd = os.open(str(tmp_path / "lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        t0 = time.monotonic()
        summarizer._acquire_codex_lock(fd, timeout=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_acquire_codex_lock_timeout_raises_when_contended(tmp_path):
    """Hold the lock in a background thread, attempt acquire with short
    timeout — should raise LockTimeoutError after the budget elapses."""
    lock_path = tmp_path / "lock"
    held = threading.Event()
    release = threading.Event()

    def hold():
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held.set()
            release.wait(timeout=10)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(timeout=5), "background holder did not acquire lock"
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            t0 = time.monotonic()
            with pytest.raises(summarizer.LockTimeoutError):
                summarizer._acquire_codex_lock(fd, timeout=1.0)
            elapsed = time.monotonic() - t0
            # poll interval is 0.5s; we should observe ~1.0-1.5s
            assert 0.9 <= elapsed <= 2.0, f"timeout took {elapsed:.2f}s"
        finally:
            os.close(fd)
    finally:
        release.set()
        t.join(timeout=5)


# ---------- _slugify ----------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hello World", "hello-world"),
        ("UPPER lower 123", "upper-lower-123"),
        ("  spaced  out  ", "spaced-out"),
        ("special!@#$%chars", "special-chars"),
        ("", "transcript"),
        ("---", "transcript"),
        ("---a---", "a"),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    assert summarizer._slugify(value) == expected
