"""Summary provider chain.

Each LLM backend (codex CLI, claude CLI, FreeLLMAPI-served models) implements
the `SummaryProvider` protocol: its `summarize()` produces raw markdown, runs
the output through `validate_and_canonicalize`, and either returns a clean
`SummaryResult` or raises a typed subclass of `ProviderError`. The caller
(`scribe.pipeline.summarizer.summarize`) walks a configured chain of
providers; the first one to return a canonical result wins.

Typed errors:
  * `ProviderUsageLimitError` — provider reports a rate/usage limit.
  * `ProviderUnavailableError` — network down, OAuth revoked, daemon missing.
  * `ProviderTimeoutError` — call exceeded per-provider timeout budget.
  * `ProviderError` (base, inc. shape-invalid responses from
    `validate_and_canonicalize`) — catch-all for everything else.

All four inherit from `ProviderError` so a single `except ProviderError` keeps
the fallback chain advancing while still letting callers dispatch on subclass
for structured logging / alerting.

The codex backend keeps its `fcntl.flock`-serialised execution path: codex's
ChatGPT OAuth refresh tokens are single-use and concurrent codex processes
inside the same container would race the refresh and revoke each other.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from scribe.obs import metrics
from scribe.pipeline.summary_validator import (
    ProviderError,
    SummaryResult,
    validate_and_canonicalize,
)

__all__ = [
    "ClaudeProvider",
    "CodexProvider",
    "FreeLLMAPIProvider",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUsageLimitError",
    "SummaryProvider",
    "SummaryResult",
    "build_provider_chain",
    "provider_class_by_name",
    "summarize_with_chain",
    "validate_and_canonicalize",
]

log = logging.getLogger("scribe.summary_providers")


# ---------- typed errors ------------------------------------------------------


class ProviderUnavailableError(ProviderError):
    """Provider could not be reached: network down, auth daemon dead, OAuth
    token revoked. `stderr_tail` (when populated) carries enough context for
    an operator alert (Telegram); kept off the base class because only the
    codex provider currently produces it."""

    def __init__(self, details: str = "", *, stderr_tail: str = "") -> None:
        super().__init__(reason="unavailable", details=details)
        self.stderr_tail = stderr_tail


class ProviderUsageLimitError(ProviderError):
    """Provider explicitly reports a rate/usage limit (codex 'usage limit',
    Anthropic 429, OpenAI proxy 429, …). Caller treats this like a soft
    failure: log warning and try the next provider."""

    def __init__(self, details: str = "") -> None:
        super().__init__(reason="usage_limit", details=details)


class ProviderTimeoutError(ProviderError):
    """Per-provider timeout exceeded (subprocess.TimeoutExpired or
    httpx.TimeoutException). Caller advances the chain."""

    def __init__(self, details: str = "") -> None:
        super().__init__(reason="timeout", details=details)


# ---------- protocol ----------------------------------------------------------


@runtime_checkable
class SummaryProvider(Protocol):
    """A provider exposes a stable `name` (for telemetry/log keys) plus
    `summarize(prompt)`. `summarize` must call `validate_and_canonicalize`
    on the raw LLM output before returning, and raise a `ProviderError`
    subclass for any unrecoverable response or transport failure."""

    name: str

    def summarize(self, prompt: str) -> SummaryResult: ...


# ---------- shared stderr signature helpers -----------------------------------

# Codex OAuth/refresh-token death signatures. Match any one → token revoked,
# a human needs to re-login inside the container.
_TOKEN_REVOKED_PATTERNS = (
    "token_revoked",
    "refresh_token_reused",
    "Encountered invalidated oauth token",
    "Your access token could not be refreshed because your refresh token",
    "Please log out and sign in again",
)

# Usage- / rate-limit signatures emitted by codex CLI and (lowercased) by
# claude CLI / proxy stderr text. Substring match against the lowercased
# stderr is sufficient — both vendors include the phrase verbatim.
_USAGE_LIMIT_PATTERNS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "too many requests",
)


def _is_token_revoked(stderr: str) -> bool:
    return any(sig in stderr for sig in _TOKEN_REVOKED_PATTERNS)


def _is_usage_limit(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return any(sig in lowered for sig in _USAGE_LIMIT_PATTERNS)


# ---------- codex provider ----------------------------------------------------


def _acquire_codex_lock(lock_fd: int, timeout: float | None) -> None:
    """Acquire LOCK_EX on `lock_fd`. `timeout=None` blocks (worker path).
    A positive timeout polls non-blocking, raising `LockTimeoutError` after
    the budget elapses (API-handler path — protects FastAPI threads from
    being pinned for the full codex window)."""
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


class LockTimeoutError(ProviderError):
    """Could not acquire the codex serialisation lock within the caller's
    budget — another codex run is in flight. Inherits from ProviderError so
    the chain treats it as a soft failure rather than a 500."""

    def __init__(self, details: str = "") -> None:
        super().__init__(reason="lock_timeout", details=details)


class CodexProvider:
    """codex CLI summariser. Shells out to `codex exec --skip-git-repo-check`
    with the prompt on stdin and the summary written to a temp file.

    Maps codex stderr → typed errors:
      * token-revoked signatures → `ProviderUnavailableError` (carries the
        stderr tail so the chain can drive a one-shot Telegram alert AFTER
        the whole chain has failed, not on every codex failure).
      * "usage limit" / "rate limit" → `ProviderUsageLimitError`.
      * `subprocess.TimeoutExpired` → `ProviderTimeoutError`.
      * anything else → `ProviderError` (catch-all, chain advances).

    The `fcntl.flock` serialisation stays codex-specific: ChatGPT OAuth
    refresh tokens are single-use and concurrent codex processes would race
    the refresh and revoke each other's tokens.
    """

    name = "codex"

    def __init__(self, settings: Any, *, lock_timeout: float | None = None) -> None:
        self._bin = settings.codex_bin
        self._model = settings.codex_model
        self._reasoning = settings.codex_reasoning
        self._lock_path = settings.codex_lock_path
        self._timeout_secs = getattr(settings, "codex_timeout_secs", 600)
        self._lock_timeout = lock_timeout

    def summarize(self, prompt: str) -> SummaryResult:
        lock_path = Path(self._lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _acquire_codex_lock(lock_fd, self._lock_timeout)
            with tempfile.TemporaryDirectory(prefix="scribe-codex-") as tmp:
                out_file = Path(tmp) / "summary.md"
                cmd = [
                    self._bin, "exec",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", f"model_reasoning_effort={self._reasoning}",
                    "-o", str(out_file),
                ]
                if self._model:
                    cmd += ["-m", self._model]
                cmd += ["-"]
                try:
                    proc = subprocess.run(
                        cmd, input=prompt, text=True, capture_output=True,
                        timeout=self._timeout_secs,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ProviderTimeoutError(
                        f"codex exec timed out after {self._timeout_secs}s"
                    ) from exc
                stderr = proc.stderr or ""
                if proc.returncode != 0 or not out_file.is_file():
                    stderr_tail = stderr or (proc.stdout or "")
                    if _is_token_revoked(stderr):
                        log.error("codex token revoked", extra={"rc": proc.returncode})
                        metrics.codex_token_revoked_total.inc()
                        raise ProviderUnavailableError(
                            f"codex OAuth token revoked: {stderr_tail[-400:]}",
                            stderr_tail=stderr_tail[-2000:],
                        )
                    if _is_usage_limit(stderr):
                        raise ProviderUsageLimitError(
                            f"codex usage limit: {stderr_tail[-400:]}"
                        )
                    raise ProviderError(
                        reason="codex_failed",
                        details=f"rc={proc.returncode}: {stderr_tail[-2000:]}",
                    )
                summary_md = out_file.read_text(encoding="utf-8").strip()
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

        if not summary_md:
            raise ProviderError(reason="empty", details="codex produced empty summary")
        # Ops rollcall reads this gauge to flag the codex CLI as `warn` after
        # >1h of silence — sampled only on the success path so a stuck or
        # revoked-token codex doesn't keep the timestamp fresh.
        metrics.last_codex_success_timestamp.set(time.time())
        return validate_and_canonicalize(summary_md)


# ---------- claude provider ---------------------------------------------------


class ClaudeProvider:
    """Anthropic CLI summariser. Invokes `claude --model <m> --effort <e> -p
    <prompt>` non-interactively; the response is on stdout.

    Maps usage-limit / rate-limit stderr to `ProviderUsageLimitError`,
    timeouts to `ProviderTimeoutError`, other failures to the generic
    `ProviderError`. Auth failures (CLI missing, token expired) surface as
    non-zero exit codes with no usage-limit signature, so they fall through
    to the generic `ProviderError` branch — the chain still advances.
    """

    name = "claude"

    def __init__(self, settings: Any, **_: Any) -> None:
        self._bin = settings.claude_bin
        self._model = settings.claude_model
        self._effort = settings.claude_effort
        self._timeout_secs = settings.claude_timeout_secs

    def summarize(self, prompt: str) -> SummaryResult:
        cmd = [
            self._bin,
            "--model", self._model,
            "--effort", self._effort,
            "-p", prompt,
        ]
        try:
            proc = subprocess.run(
                cmd, text=True, capture_output=True, timeout=self._timeout_secs,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                f"claude timed out after {self._timeout_secs}s"
            ) from exc
        except FileNotFoundError as exc:
            raise ProviderUnavailableError(
                f"claude binary not found: {self._bin}"
            ) from exc
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            tail = stderr or (proc.stdout or "")
            if _is_usage_limit(stderr):
                raise ProviderUsageLimitError(f"claude usage limit: {tail[-400:]}")
            raise ProviderError(
                reason="claude_failed",
                details=f"rc={proc.returncode}: {tail[-2000:]}",
            )
        summary_md = (proc.stdout or "").strip()
        if not summary_md:
            raise ProviderError(reason="empty", details="claude produced empty stdout")
        return validate_and_canonicalize(summary_md)


# ---------- freellmapi provider -----------------------------------------------


class FreeLLMAPIProvider:
    """OpenAI Chat-Completions-compatible homelab proxy. Sends a single user
    message containing the merged prompt+transcript and validates the
    returned `choices[0].message.content`.

    Maps:
      * 429 → `ProviderUsageLimitError`.
      * 5xx → `ProviderError` (catch-all, chain advances).
      * `httpx.TimeoutException` → `ProviderTimeoutError`.
      * other transport errors (DNS, refused, TLS) → `ProviderUnavailableError`.

    `freellmapi_api_key` must be supplied via env (sourced from Infisical at
    runtime; see `compose.yaml`). Empty key fails fast as unavailable rather
    than emitting an unauthenticated request that the proxy will 401.
    """

    name = "freellmapi"

    def __init__(self, settings: Any, **_: Any) -> None:
        self._base_url = settings.freellmapi_base_url.rstrip("/")
        self._api_key = settings.freellmapi_api_key
        self._model = settings.freellmapi_model
        self._timeout_secs = settings.freellmapi_timeout_secs

    def summarize(self, prompt: str) -> SummaryResult:
        if not self._api_key:
            raise ProviderUnavailableError("freellmapi api key not configured")
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            with httpx.Client(timeout=self._timeout_secs) as client:
                resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"freellmapi timed out after {self._timeout_secs}s"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"freellmapi transport error: {exc}"
            ) from exc
        if resp.status_code == 429:
            raise ProviderUsageLimitError(
                f"freellmapi 429: {resp.text[:400]}"
            )
        if 500 <= resp.status_code < 600:
            raise ProviderError(
                reason="freellmapi_5xx",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )
        if resp.status_code != 200:
            raise ProviderError(
                reason="freellmapi_http",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                reason="freellmapi_bad_response",
                details=f"{type(exc).__name__}: {exc!s}"[:400],
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                reason="empty",
                details="freellmapi returned empty content",
            )
        return validate_and_canonicalize(content.strip())


# ---------- chain assembly ----------------------------------------------------


provider_class_by_name: dict[str, type[SummaryProvider]] = {
    "codex": CodexProvider,
    "claude": ClaudeProvider,
    "freellmapi": FreeLLMAPIProvider,
}


def build_provider_chain(
    settings: Any, *, lock_timeout: float | None = None
) -> list[SummaryProvider]:
    """Instantiate the configured provider chain. Unknown names are logged
    and skipped — operators can disable a provider by removing it from
    `SCRIBE_SUMMARY_PROVIDERS` without touching code."""
    chain: list[SummaryProvider] = []
    for raw_name in settings.summary_providers:
        name = raw_name.strip().lower()
        cls = provider_class_by_name.get(name)
        if cls is None:
            log.warning("unknown summary provider %r — skipping", raw_name)
            continue
        chain.append(cls(settings, lock_timeout=lock_timeout))
    return chain


def summarize_with_chain(
    providers: list[SummaryProvider], prompt: str
) -> SummaryResult:
    """Try each provider in order. Catches `ProviderError` (and its typed
    subclasses) and advances; any other exception propagates immediately.

    Raises `ProviderError(reason="chain_exhausted")` if every provider
    raised, or `ProviderError(reason="no_providers")` if the chain is empty.

    The richer logging + Telegram-alert behaviour lives in
    `scribe.pipeline.summarizer.summarize` — this helper stays a small,
    test-friendly primitive that the validator tests exercise directly.
    """
    if not providers:
        raise ProviderError(reason="no_providers", details="empty provider chain")

    last_error: ProviderError | None = None
    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        try:
            return provider.summarize(prompt)
        except ProviderError as exc:
            log.warning(
                "summary provider %s failed: %s",
                name,
                exc.details or exc.reason,
            )
            last_error = exc
            continue

    assert last_error is not None  # loop ran at least once
    raise ProviderError(
        reason="chain_exhausted",
        details=f"all providers failed; last={last_error.reason}: {last_error.details}",
    )
