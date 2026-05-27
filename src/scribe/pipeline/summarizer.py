"""Summarizer — codex CLI (ChatGPT Pro subscription).

MVP backend: shells out to `codex exec`. Auth is the container's own
`/root/.codex/auth.json` (own login per host — sharing auth.json across
processes causes refresh-token races that revoke both copies).

Resilience (PRD §4.4):
  * `fcntl.flock` on settings.codex_lock_path serialises codex invocations
    inside the container so two summary runs can't fight over the refresh.
  * Token-revocation signatures in codex stderr are mapped to
    `CodexTokenRevokedError`; the worker uses the exception class to
    distinguish "fix the token, retry" from "real summarizer bug".
  * When a token-revocation is detected and admin Telegram creds are
    configured, scribe fires off a one-shot admin alert so the operator
    knows to re-login (`docker exec -it scribe codex login --device-auth`).

Notes from bring-up (2026-05-14):
  * gpt-5.4-nano/mini are NOT available via a ChatGPT-account codex; the model
    is whatever the container's codex config selects (gpt-5.x family).
  * reasoning_effort must be >= "low" — "minimal" is rejected by the API
    because codex's default tools (image_gen, web_search) require it.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.obs import metrics
from scribe.pipeline import prompts
from scribe.pipeline.summary_validator import (
    ProviderError,
    SummaryResult,
    validate_and_canonicalize,
)

log = logging.getLogger("scribe.summarizer")

# Re-exported so callers (and tests) can still write
# `from scribe.pipeline.summarizer import SummaryResult` / `ProviderError`.
__all__ = [
    "CodexTokenRevokedError",
    "LockTimeoutError",
    "ProviderError",
    "SummarizeError",
    "SummaryResult",
    "summarize",
]

_TAG_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TAG_REPLACEMENTS = {
    "bytovaya-scena": "everyday-scene",
    "detskaya-rech": "child-speech",
    "dok-stancii": "docking-stations",
    "internet-kultura": "internet-culture",
    "intuiciya": "intuition",
    "karera": "career",
    "periferia": "peripherals",
    "plagin": "plugins",
    "poisk": "search",
    "pokupka": "buying-guide",
    "predprinimatelstvo": "entrepreneurship",
    "produktivnost": "productivity",
    "smertnost": "mortality",
    "stiv-dzhobs": "steve-jobs",
    "usb-huby": "usb-hubs",
}
_SHORT_DESCRIPTION_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
}
_REJECTED_TAGS = {
    "auto-generated",
    "example",
    "tag1",
    "tag2",
    "tag3",
    "transcript",
}

# Signatures emitted by codex on OAuth/refresh-token problems. Any of these
# in stderr → the token is dead and a human needs to re-login.
_TOKEN_REVOKED_PATTERNS = (
    "token_revoked",
    "refresh_token_reused",
    "Encountered invalidated oauth token",
    "Your access token could not be refreshed because your refresh token",
    "Please log out and sign in again",
)


class SummarizeError(RuntimeError):
    pass


class CodexTokenRevokedError(SummarizeError):
    """codex OAuth token is revoked / un-refreshable. Operator must re-login
    inside the container before any further summary runs will succeed."""


class LockTimeoutError(SummarizeError):
    """Could not acquire the codex serialisation lock within the caller's
    timeout — another codex run is in flight. Callers should surface this as
    a 503-ish "try again later" rather than a generic 500."""


def _acquire_codex_lock(lock_fd: int, timeout: float | None) -> None:
    """Acquire LOCK_EX on `lock_fd`. `timeout=None` blocks indefinitely (worker
    path). A positive timeout polls non-blocking and raises LockTimeoutError
    if the wait exceeds the budget (API-handler path — protects the FastAPI
    thread pool from being pinned for the full 600s codex window)."""
    if timeout is None:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeoutError(
                    f"could not acquire codex lock within {timeout:.0f}s"
                ) from None
            time.sleep(0.5)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "transcript"


def _normalize_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        slug = _slugify(value)
        slug = _TAG_REPLACEMENTS.get(slug, slug)
        if slug in _REJECTED_TAGS or not _TAG_SLUG_RE.fullmatch(slug):
            continue
        if slug not in seen:
            tags.append(slug)
            seen.add(slug)
    return tags


def _short_description_language_name(code: str) -> str:
    return _SHORT_DESCRIPTION_LANGUAGE_NAMES.get(code, "Russian")


def _is_token_revoked(stderr: str) -> bool:
    return any(sig in stderr for sig in _TOKEN_REVOKED_PATTERNS)


def _alert_token_revoked(stderr_tail: str) -> None:
    """Fire-and-forget admin Telegram alert. Idempotency / debouncing left to
    the operator — codex stays broken until they re-login anyway, so repeated
    failures naturally retry the alert too."""
    msg = (
        "🚨 scribe: codex OAuth token revoked. Re-login required:\n"
        "  docker exec -it scribe codex login --device-auth\n\n"
        f"codex stderr tail:\n{stderr_tail[-400:]}"
    )
    send_admin_alert(msg)


def summarize(
    transcript_md: str,
    *,
    title: str,
    transcript_slug: str | None = None,
    summary_date: dt.date | None = None,
    lock_timeout: float | None = None,
    prompt_version: str | None = None,
    prompt_body: str | None = None,
) -> SummaryResult:
    """Produce a Russian analytical summary of `transcript_md` via codex CLI."""
    summary_date = summary_date or dt.date.today()
    transcript_slug = transcript_slug or _slugify(title)
    try:
        if prompt_body is not None:
            template = prompt_body
        elif prompt_version:
            template = prompts.read_prompt(prompt_version)
        else:
            template = prompts.read_active_prompt()[1]
    except prompts.PromptError as exc:
        raise SummarizeError(str(exc)) from exc
    prompt = (
        template.replace("{date}", summary_date.isoformat())
        .replace("{transcript_slug}", transcript_slug)
        .replace("{short_description_language_code}", settings.short_description_language)
        .replace(
            "{short_description_language_name}",
            _short_description_language_name(settings.short_description_language),
        )
        + "\n\nTranscript to summarize:\n\n"
        + transcript_md
    )

    lock_path = Path(settings.codex_lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _acquire_codex_lock(lock_fd, lock_timeout)
        with tempfile.TemporaryDirectory(prefix="scribe-codex-") as tmp:
            out_file = Path(tmp) / "summary.md"
            cmd = [
                settings.codex_bin, "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "-c", f"model_reasoning_effort={settings.codex_reasoning}",
                "-o", str(out_file),
            ]
            if settings.codex_model:
                cmd += ["-m", settings.codex_model]
            cmd += ["-"]  # read the prompt from stdin
            try:
                proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=600)
            except subprocess.TimeoutExpired as exc:
                raise SummarizeError("codex exec timed out after 600s") from exc
            stderr = proc.stderr or ""
            if proc.returncode != 0 or not out_file.is_file():
                stderr_tail = stderr or proc.stdout
                if _is_token_revoked(stderr):
                    log.error("codex token revoked", extra={"rc": proc.returncode})
                    metrics.codex_token_revoked_total.inc()
                    _alert_token_revoked(stderr_tail)
                    raise CodexTokenRevokedError(
                        "codex OAuth token revoked — operator must re-login. "
                        f"Last codex stderr: {stderr_tail[-400:]}"
                    )
                raise SummarizeError(
                    f"codex exec failed (rc={proc.returncode}):\n{stderr_tail[-2000:]}"
                )
            summary_md = out_file.read_text(encoding="utf-8").strip()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    if not summary_md:
        raise SummarizeError("codex exec produced an empty summary")
    # Ops rollcall reads this gauge to flag the codex CLI as `warn` after >1h
    # of silence. Sampled only on the success path (codex CLI didn't error)
    # so a stuck/revoked-token codex doesn't keep the timestamp fresh. A
    # shape-invalid response still counts as a successful invocation from the
    # auth/CLI side — the validator handles content-quality fallthrough below.
    metrics.last_codex_success_timestamp.set(time.time())

    # Shape validation + canonicalisation. Raises ProviderError(shape_invalid)
    # if the output can't be repaired, which the chain treats like a timeout.
    result = validate_and_canonicalize(summary_md)
    # Apply codex-specific tag content rules (slug regex, transliteration,
    # rejected placeholders) on top of the shape-only validator output.
    return SummaryResult(
        summary_md=result.summary_md,
        tags=_normalize_tags(result.tags),
        short_description=result.short_description,
    )
