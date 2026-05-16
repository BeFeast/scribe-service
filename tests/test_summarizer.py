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


def test_normalize_tags_replaces_transliteration_and_keeps_technical_tags() -> None:
    assert summarizer._normalize_tags(
        [
            "bytovaya-scena",
            "detskaya-rech",
            "dok-stancii",
            "internet-kultura",
            "intuiciya",
            "karera",
            "periferia",
            "plagin",
            "poisk",
            "pokupka",
            "predprinimatelstvo",
            "produktivnost",
            "smertnost",
            "stiv-dzhobs",
            "usb-huby",
            "Apple",
            "ai-economics",
            "ai-security",
            "apple-silicon",
            "backups",
            "Claude Code",
            "coding-agent",
            "tag1",
        ]
    ) == [
        "everyday-scene",
        "child-speech",
        "docking-stations",
        "internet-culture",
        "intuition",
        "career",
        "peripherals",
        "plugins",
        "search",
        "buying-guide",
        "entrepreneurship",
        "productivity",
        "mortality",
        "steve-jobs",
        "usb-hubs",
        "apple",
        "ai-economics",
        "ai-security",
        "apple-silicon",
        "backups",
        "claude-code",
        "coding-agent",
    ]


def test_summarize_reads_active_prompt_each_invocation(tmp_path, monkeypatch):
    """The worker path should pick up active-version changes without restart."""
    for version in prompts.PROMPT_VERSIONS:
        (tmp_path / f"transcript-summary.{version}.md").write_text(
            f"Prompt {version} {{date}} {{transcript_slug}}\n\n## TL;DR\n\nx\n\n## Details\n\ny\n",
            encoding="utf-8",
        )
    active_path = tmp_path / "transcript-summary.active"
    active_path.write_text("v1\n", encoding="utf-8")

    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")

    seen_prompts: list[str] = []

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002
        seen_prompts.append(input)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("---\ntags: [dry-run]\n---\n\nsummary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summarizer.subprocess, "run", fake_run)

    summarizer.summarize("Transcript", title="First Title")
    active_path.write_text("v2\n", encoding="utf-8")
    summarizer.summarize("Transcript", title="Second Title")

    assert "Prompt v1" in seen_prompts[0]
    assert "Prompt v2" in seen_prompts[1]


def test_summarize_parses_short_description_from_frontmatter(tmp_path, monkeypatch):
    (tmp_path / "transcript-summary.v1.md").write_text(
        "Prompt {date} {transcript_slug}",
        encoding="utf-8",
    )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")

    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            "---\n"
            "tags: [systems, ai]\n"
            'short_description: "A fluent card description for the library. It ends cleanly."\n'
            "---\n\n"
            "# Summary\n\nFull body stays intact.",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summarizer.subprocess, "run", fake_run)

    result = summarizer.summarize("Transcript", title="Parser Test")

    assert result.tags == ["systems", "ai"]
    assert result.short_description == "A fluent card description for the library. It ends cleanly."
    assert "Full body stays intact." in result.summary_md
