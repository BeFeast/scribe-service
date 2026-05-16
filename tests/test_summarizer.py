"""Tests for scribe.pipeline.summarizer — token detection + lock acquisition.

Focused on the resilience surface that PR #1 introduced: signature-matching
against codex stderr, and the lock_timeout / LockTimeoutError contract on
`_acquire_codex_lock`. The actual `summarize()` call shells out to codex so
it is not exercised here — covered by live smoke tests on the deployed
container."""
from __future__ import annotations

import fcntl
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scribe.pipeline import prompts, summarizer

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


def test_summarize_reads_active_prompt_version_each_invocation(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "transcript-summary.v1.md").write_text(
        "Prompt V1\n\n## TL;DR\n\nUse v1.\n\n## Details\n\n{date} {transcript_slug}",
        encoding="utf-8",
    )
    (prompts_dir / "transcript-summary.v2.md").write_text(
        "Prompt V2\n\n## TL;DR\n\nUse v2.\n\n## Details\n\n{date} {transcript_slug}",
        encoding="utf-8",
    )
    (prompts_dir / "transcript-summary.v3.md").write_text(
        "Prompt V3\n\n## TL;DR\n\nUse v3.\n\n## Details\n\n{date} {transcript_slug}",
        encoding="utf-8",
    )
    active = prompts_dir / "transcript-summary.active"
    active.write_text("v1\n", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")

    seen_prompts: list[str] = []

    def _fake_run(cmd, *, input, text, capture_output, timeout):
        assert text is True
        assert capture_output is True
        assert timeout == 600
        seen_prompts.append(input)
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text("tags: [test]\n\nsummary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summarizer.subprocess, "run", _fake_run)

    summarizer.summarize("Transcript", title="Title")
    active.write_text("v2\n", encoding="utf-8")
    summarizer.summarize("Transcript", title="Title")

    assert "Prompt V1" in seen_prompts[0]
    assert "Prompt V2" in seen_prompts[1]
