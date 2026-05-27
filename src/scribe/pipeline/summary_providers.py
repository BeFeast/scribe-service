"""Summary provider chain + per-provider circuit breaker.

Each LLM backend (codex, claude, freellmapi-served models) implements the
`SummaryProvider` protocol: its `summarize()` produces raw markdown, runs the
output through `validate_and_canonicalize`, and either returns a clean
`SummaryResult` or raises `ProviderError`. `summarize_with_chain` iterates the
providers in order, catches `ProviderError` (treating a shape-invalid response
identically to a timeout), and falls through to the next provider.

The chain consults a per-provider `CircuitBreaker` before each call. After
`threshold` consecutive trip-relevant failures (`ProviderUsageLimitError`,
`ProviderUnavailableError`, `ProviderTimeoutError`) inside a sliding `window`,
the provider enters `tripped` state and the chain skips it without a call for
`cooldown` seconds. The first call after the cooldown elapses is a `half_open`
trial; success returns the breaker to `closed`, failure restarts the cooldown.

Breaker state is in-process. On container restart everyone resets to `closed`
— restart is a strong signal that conditions may have changed.

Concrete provider classes (`CodexProvider`, `ClaudeProvider`,
`FreeLLMAPIProvider`) live below the chain machinery and are the production
backends wired up by `build_provider_chain`. The legacy entrypoint
`scribe.pipeline.summarizer.summarize` builds the chain at call time and
translates `ProviderError(chain_exhausted)` into `SummarizeError`.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from scribe.config import Settings
from scribe.config import settings as default_settings
from scribe.obs import metrics
from scribe.pipeline.summary_validator import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsageLimitError,
    SummaryResult,
    validate_and_canonicalize,
)

__all__ = [
    "CircuitBreaker",
    "ClaudeProvider",
    "CodexProvider",
    "FreeLLMAPIProvider",
    "PROVIDER_REGISTRY",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUsageLimitError",
    "SummaryProvider",
    "SummaryResult",
    "build_provider_chain",
    "get_breaker",
    "summarize_with_chain",
    "validate_and_canonicalize",
]

log = logging.getLogger("scribe.summary_providers")

_STATE_CLOSED = "closed"
_STATE_HALF_OPEN = "half_open"
_STATE_TRIPPED = "tripped"
_STATE_VALUES = {_STATE_CLOSED: 0, _STATE_HALF_OPEN: 1, _STATE_TRIPPED: 2}

_TRIP_RELEVANT_OUTCOMES = frozenset({"usage_limit", "unavailable", "timeout"})


def _now() -> float:
    """Indirection over `time.monotonic` so tests can fast-forward time."""
    return time.monotonic()


def _classify_error(exc: ProviderError) -> tuple[str, bool]:
    """Map a ProviderError to (outcome_label, trip_relevant)."""
    if isinstance(exc, ProviderUsageLimitError):
        return "usage_limit", True
    if isinstance(exc, ProviderTimeoutError):
        return "timeout", True
    if isinstance(exc, ProviderUnavailableError):
        return "unavailable", True
    return "error", False


@runtime_checkable
class SummaryProvider(Protocol):
    """A provider must expose a stable `name` for telemetry plus `summarize`.

    `summarize` must call `validate_and_canonicalize` on the raw LLM output
    before returning, and raise `ProviderError` for any unrecoverable
    response shape.
    """

    name: str

    def summarize(self, prompt: str) -> SummaryResult: ...


class CircuitBreaker:
    """Per-provider sliding-window failure tracker.

    Thread-safe. The same instance is consulted by every chain run for a given
    provider name. State transitions:

      closed   ──[threshold trip-relevant fails within window]──▶ tripped
      tripped  ──[cooldown elapsed, next call]──▶ half_open
      half_open──[trial success]──▶ closed
      half_open──[trial failure]──▶ tripped (cooldown restarts)

    In `half_open`, only the first caller is granted the trial; concurrent
    callers see `skip` until the trial resolves.
    """

    def __init__(
        self,
        name: str,
        *,
        window_secs: float,
        threshold: int,
        cooldown_secs: float,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.name = name
        self.window_secs = float(window_secs)
        self.threshold = int(threshold)
        self.cooldown_secs = float(cooldown_secs)

        self._lock = threading.Lock()
        self._state: str = _STATE_CLOSED
        self._tripped_until: float = 0.0
        self._trial_in_progress: bool = False
        self._outcomes: deque[tuple[float, str]] = deque(maxlen=self.threshold)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def acquire(self) -> str:
        """Decide whether the next call is allowed.

        Returns one of:
          * `"allow"`  — provider is closed; caller proceeds normally.
          * `"trial"`  — provider is half_open and this caller owns the trial.
          * `"skip"`   — provider is tripped (or another thread holds the trial).
        """
        with self._lock:
            now = _now()
            if self._state == _STATE_TRIPPED:
                if now >= self._tripped_until:
                    self._state = _STATE_HALF_OPEN
                else:
                    return "skip"

            if self._state == _STATE_HALF_OPEN:
                if self._trial_in_progress:
                    return "skip"
                self._trial_in_progress = True
                return "trial"

            return "allow"

    def record(self, outcome: str, *, mode: str) -> None:
        """Record the outcome of a call that was previously acquired with `mode`.

        `outcome` must be one of the metric result labels (`success`,
        `usage_limit`, `unavailable`, `timeout`, `error`). The breaker treats
        anything in `_TRIP_RELEVANT_OUTCOMES` as a trip signal.

        `mode` is the value returned by the matching `acquire()` call. A
        `"trial"` outcome always resolves the half_open state (success →
        closed, anything else → tripped, cooldown restarts).
        """
        with self._lock:
            now = _now()
            self._outcomes.append((now, outcome))

            if mode == "trial":
                self._trial_in_progress = False
                if outcome == "success":
                    self._state = _STATE_CLOSED
                else:
                    self._state = _STATE_TRIPPED
                    self._tripped_until = now + self.cooldown_secs
                return

            if outcome in _TRIP_RELEVANT_OUTCOMES and len(self._outcomes) >= self.threshold:
                cutoff = now - self.window_secs
                recent = list(self._outcomes)[-self.threshold :]
                if all(ts >= cutoff and oc in _TRIP_RELEVANT_OUTCOMES for ts, oc in recent):
                    self._state = _STATE_TRIPPED
                    self._tripped_until = now + self.cooldown_secs


_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(name: str) -> CircuitBreaker:
    """Return the module-level breaker for `name`, creating it on first use.

    Configuration is read from `settings` at creation time; in-flight breakers
    are not reconfigured if settings change later, which matches the in-process
    "restart to reset" contract documented in the issue.
    """
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                window_secs=default_settings.summary_breaker_window_secs,
                threshold=default_settings.summary_breaker_threshold,
                cooldown_secs=default_settings.summary_breaker_cooldown_secs,
            )
            _breakers[name] = breaker
            metrics.summary_provider_state.labels(provider=name).set(
                _STATE_VALUES[breaker.state]
            )
        return breaker


def _reset_breakers_for_test() -> None:
    """Drop all in-process breakers. Test-only helper."""
    with _breakers_lock:
        _breakers.clear()


def _publish_state(breaker: CircuitBreaker) -> None:
    metrics.summary_provider_state.labels(provider=breaker.name).set(
        _STATE_VALUES[breaker.state]
    )


def summarize_with_chain(
    providers: list[SummaryProvider],
    prompt: str,
    *,
    attempts: list[tuple[str, str]] | None = None,
) -> SummaryResult:
    """Try each provider in order, consulting its circuit breaker first.

    Tripped providers are skipped without a call and logged as
    `scribe.summary.provider_skipped_tripped`. Trip-relevant failures
    (`ProviderUsageLimitError`, `ProviderUnavailableError`,
    `ProviderTimeoutError`) and the generic `ProviderError` are caught and the
    chain advances; any other exception (auth, runtime error) propagates
    immediately.

    `attempts`, if supplied, is appended in-place with `(provider_name,
    outcome_description)` per attempted provider so the caller can build a
    multi-provider error message. It is also exposed on the raised
    `ProviderError(chain_exhausted)` as `.attempts`.

    Raises `ProviderError(reason="chain_exhausted")` if every provider failed
    or was skipped, or `ProviderError(reason="no_providers")` if the chain is
    empty.
    """
    if not providers:
        raise ProviderError(reason="no_providers", details="empty provider chain")

    local_attempts: list[tuple[str, str]] = attempts if attempts is not None else []

    last_error: ProviderError | None = None
    had_fallback: bool = False

    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        breaker = get_breaker(name)
        mode = breaker.acquire()
        _publish_state(breaker)

        if mode == "skip":
            log.info(
                "scribe.summary.provider_skipped_tripped",
                extra={"provider": name, "breaker_state": breaker.state},
            )
            metrics.summary_provider_calls_total.labels(
                provider=name, result="skipped_tripped"
            ).inc()
            local_attempts.append((name, "skipped_tripped"))
            had_fallback = True
            continue

        try:
            result = provider.summarize(prompt)
        except ProviderError as exc:
            outcome, _ = _classify_error(exc)
            metrics.summary_provider_calls_total.labels(
                provider=name, result=outcome
            ).inc()
            breaker.record(outcome, mode=mode)
            _publish_state(breaker)
            log.warning(
                "scribe.summary.provider_fallback",
                extra={
                    "provider": name,
                    "outcome": outcome,
                    "reason": exc.reason,
                    "details": exc.details,
                },
            )
            local_attempts.append((name, f"{outcome}: {exc.details or exc.reason}"))
            last_error = exc
            had_fallback = True
            continue

        metrics.summary_provider_calls_total.labels(
            provider=name, result="success"
        ).inc()
        breaker.record("success", mode=mode)
        _publish_state(breaker)
        local_attempts.append((name, "success"))

        chain_label = "success_after_fallback" if had_fallback else "success_first"
        metrics.summary_chain_outcome_total.labels(outcome=chain_label).inc()
        log.info(
            "scribe.summary.provider_success",
            extra={"provider": name, "chain_outcome": chain_label},
        )
        return result

    metrics.summary_chain_outcome_total.labels(outcome="all_failed").inc()
    if last_error is None:
        err = ProviderError(
            reason="chain_exhausted",
            details="all providers skipped (tripped)",
        )
    else:
        err = ProviderError(
            reason="chain_exhausted",
            details=f"all providers failed; last={last_error.reason}: {last_error.details}",
        )
    err.attempts = list(local_attempts)  # type: ignore[attr-defined]
    raise err


# ---------- concrete provider implementations --------------------------------

_CODEX_USAGE_LIMIT_PATTERNS = ("usage limit", "rate limit", "quota exceeded")
_CODEX_TOKEN_REVOKED_PATTERNS = (
    "token_revoked",
    "refresh_token_reused",
    "Encountered invalidated oauth token",
    "Your access token could not be refreshed because your refresh token",
    "Please log out and sign in again",
)
_CLAUDE_USAGE_LIMIT_PATTERNS = (
    "usage limit",
    "rate limit",
    "quota exceeded",
    "Claude AI usage limit reached",
    "5-hour limit reached",
    "weekly limit reached",
)
_CLAUDE_UNAVAILABLE_PATTERNS = (
    "command not found",
    "not logged in",
    "authentication required",
    "please run `claude login`",
)


def _matches_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(n.lower() in lowered for n in needles)


class CodexProvider:
    """codex CLI backend. Serialised via `fcntl.flock` on
    `settings.codex_lock_path` so concurrent codex runs do not race the
    single-use OAuth refresh token.

    Maps codex stderr signatures into typed `ProviderError` subclasses:
      * token_revoked / refresh_token_reused / "Please log out and sign in
        again" → `ProviderUnavailableError` (caller may also fire the operator
        Telegram alert after the whole chain fails).
      * "usage limit" / "rate limit" / "quota exceeded" → `ProviderUsageLimitError`.
      * subprocess timeout → `ProviderTimeoutError`.
      * Any other non-zero return → generic `ProviderError`.
    """

    name = "codex"

    def __init__(self, settings_obj: Settings | None = None) -> None:
        self._settings = settings_obj or default_settings
        self.last_token_revoked_stderr: str | None = None

    def summarize(self, prompt: str) -> SummaryResult:
        s = self._settings
        lock_path = Path(s.codex_lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            with tempfile.TemporaryDirectory(prefix="scribe-codex-") as tmp:
                out_file = Path(tmp) / "summary.md"
                cmd = [
                    s.codex_bin, "exec",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", f"model_reasoning_effort={s.codex_reasoning}",
                    "-o", str(out_file),
                ]
                if s.codex_model:
                    cmd += ["-m", s.codex_model]
                cmd += ["-"]  # read prompt from stdin
                try:
                    proc = subprocess.run(
                        cmd,
                        input=prompt,
                        text=True,
                        capture_output=True,
                        timeout=s.codex_timeout_secs,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ProviderTimeoutError(
                        reason="timeout",
                        details=f"codex exec timed out after {s.codex_timeout_secs}s",
                    ) from exc
                stderr = proc.stderr or ""
                if proc.returncode != 0 or not out_file.is_file():
                    stderr_tail = stderr or proc.stdout or ""
                    if _matches_any(stderr, _CODEX_TOKEN_REVOKED_PATTERNS):
                        log.error("codex token revoked", extra={"rc": proc.returncode})
                        metrics.codex_token_revoked_total.inc()
                        self.last_token_revoked_stderr = stderr_tail
                        raise ProviderUnavailableError(
                            reason="codex_token_revoked",
                            details=f"OAuth token revoked: {stderr_tail[-400:]}",
                        )
                    if _matches_any(stderr, _CODEX_USAGE_LIMIT_PATTERNS):
                        raise ProviderUsageLimitError(
                            reason="codex_usage_limit",
                            details=stderr_tail[-400:],
                        )
                    raise ProviderError(
                        reason="codex_error",
                        details=f"rc={proc.returncode}: {stderr_tail[-2000:]}",
                    )
                summary_md = out_file.read_text(encoding="utf-8").strip()
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

        if not summary_md:
            raise ProviderError(reason="empty_response", details="codex produced empty output")
        metrics.last_codex_success_timestamp.set(time.time())
        return validate_and_canonicalize(summary_md)


class ClaudeProvider:
    """Claude CLI backend. Invokes `claude --model <m> --effort <e> -p <prompt>`
    non-interactively. Detects usage-limit / unavailable stderr substrings and
    maps them to the matching `ProviderError` subclass.
    """

    name = "claude"

    def __init__(self, settings_obj: Settings | None = None) -> None:
        self._settings = settings_obj or default_settings

    def summarize(self, prompt: str) -> SummaryResult:
        s = self._settings
        cmd = [
            s.claude_bin,
            "--model", s.claude_model,
            "--effort", s.claude_effort,
            "-p", prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=s.claude_timeout_secs,
            )
        except FileNotFoundError as exc:
            raise ProviderUnavailableError(
                reason="claude_missing",
                details=f"{s.claude_bin} not found on PATH",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderTimeoutError(
                reason="timeout",
                details=f"claude timed out after {s.claude_timeout_secs}s",
            ) from exc

        stderr = proc.stderr or ""
        if proc.returncode != 0:
            tail = stderr or proc.stdout or ""
            if _matches_any(stderr, _CLAUDE_USAGE_LIMIT_PATTERNS):
                raise ProviderUsageLimitError(
                    reason="claude_usage_limit",
                    details=tail[-400:],
                )
            if _matches_any(stderr, _CLAUDE_UNAVAILABLE_PATTERNS):
                raise ProviderUnavailableError(
                    reason="claude_unavailable",
                    details=tail[-400:],
                )
            raise ProviderError(
                reason="claude_error",
                details=f"rc={proc.returncode}: {tail[-2000:]}",
            )

        summary_md = (proc.stdout or "").strip()
        if not summary_md:
            raise ProviderError(
                reason="empty_response",
                details="claude produced empty output",
            )
        return validate_and_canonicalize(summary_md)


class FreeLLMAPIProvider:
    """FreeLLMAPI / OpenAI-compatible chat completions backend.

    POSTs to `${base_url}/chat/completions` with bearer auth and returns the
    first choice's content. 429 → `ProviderUsageLimitError`; 5xx →
    `ProviderUnavailableError`; httpx timeout → `ProviderTimeoutError`; any
    other transport error → `ProviderError`.
    """

    name = "freellmapi"

    def __init__(self, settings_obj: Settings | None = None) -> None:
        self._settings = settings_obj or default_settings

    def summarize(self, prompt: str) -> SummaryResult:
        s = self._settings
        if not s.freellmapi_api_key.strip():
            raise ProviderUnavailableError(
                reason="freellmapi_no_api_key",
                details="SCRIBE_FREELLMAPI_API_KEY not configured",
            )
        url = f"{s.freellmapi_base_url.rstrip('/')}/chat/completions"
        body = {
            "model": s.freellmapi_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {s.freellmapi_api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(
                url,
                content=json.dumps(body),
                headers=headers,
                timeout=s.freellmapi_timeout_secs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                reason="timeout",
                details=f"freellmapi timed out after {s.freellmapi_timeout_secs}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                reason="freellmapi_transport_error",
                details=str(exc),
            ) from exc

        if resp.status_code == 429:
            raise ProviderUsageLimitError(
                reason="freellmapi_usage_limit",
                details=resp.text[:400],
            )
        if 500 <= resp.status_code < 600:
            raise ProviderUnavailableError(
                reason="freellmapi_5xx",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )
        if resp.status_code >= 400:
            raise ProviderError(
                reason="freellmapi_http_error",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )

        try:
            data: dict[str, Any] = resp.json()
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                reason="freellmapi_bad_response",
                details=f"could not parse choices[0].message.content: {exc}",
            ) from exc

        summary_md = (content or "").strip()
        if not summary_md:
            raise ProviderError(
                reason="empty_response",
                details="freellmapi returned empty content",
            )
        return validate_and_canonicalize(summary_md)


PROVIDER_REGISTRY: dict[str, type[SummaryProvider]] = {
    "codex": CodexProvider,
    "claude": ClaudeProvider,
    "freellmapi": FreeLLMAPIProvider,
}


def build_provider_chain(
    settings_obj: Settings | None = None,
) -> list[SummaryProvider]:
    """Instantiate the configured provider chain.

    Reads provider names from `settings.summary_providers`. Unknown names raise
    `ValueError` rather than being silently dropped — a typo in env should be
    surfaced loudly during process start, not buried in a fallback path.
    """
    s = settings_obj or default_settings
    names: list[str] = list(s.summary_providers) if s.summary_providers else []
    unknown = [n for n in names if n not in PROVIDER_REGISTRY]
    if unknown:
        raise ValueError(
            "unknown summary providers in SCRIBE_SUMMARY_PROVIDERS: "
            + ", ".join(unknown)
            + f". Known providers: {sorted(PROVIDER_REGISTRY)}"
        )
    return [PROVIDER_REGISTRY[name](s) for name in names]
