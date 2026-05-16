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
from dataclasses import dataclass
from pathlib import Path

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.obs import metrics
from scribe.pipeline import prompts

log = logging.getLogger("scribe.summarizer")

_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)

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


@dataclass
class SummaryResult:
    summary_md: str
    tags: list[str]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "transcript"


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
    # of silence. Sampled only on the success path so a stuck/revoked-token
    # codex doesn't keep the timestamp fresh.
    metrics.last_codex_success_timestamp.set(time.time())
    # strip accidental ``` fences wrapping the whole output
    if summary_md.startswith("```"):
        lines = summary_md.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            summary_md = "\n".join(lines[1:-1]).strip()

    tags: list[str] = []
    match = _TAGS_RE.search(summary_md)
    if match:
        tags = [t.strip().strip("\"'") for t in match.group(1).split(",") if t.strip()]
    return SummaryResult(summary_md=summary_md, tags=tags)
