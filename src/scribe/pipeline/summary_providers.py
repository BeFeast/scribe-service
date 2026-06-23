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

Concrete provider classes live below the chain machinery and are the
production backends wired up by `build_provider_chain`. The default path is
direct OpenAI-compatible HTTP via `OpenAICompatibleProvider` (#388): one
generic `POST {base_url}/chat/completions` primitive instantiated several times
with different names/models — `freellmapi` and `ollama-cloud` are both
instances. The CLI harnesses `CodexProvider` / `ClaudeProvider` remain available
as optional providers (a summary is one prompt → markdown, so the heavy
agentic harness is not needed on the main path). The chain is configured as a
list of `provider:model` entries; `build_provider_chain` parses them (splitting
on the first `:`) and looks each provider up in `PROVIDER_REGISTRY`. The legacy
entrypoint `scribe.pipeline.summarizer.summarize` builds the chain at call time
and translates `ProviderError(chain_exhausted)` into `SummarizeError`.
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
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from scribe.config import Settings
from scribe.config import settings as default_settings
from scribe.obs import metrics
from scribe.pipeline.summary_map_reduce import map_reduce_summarize
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
    "OllamaCloudProvider",
    "OpenAICompatibleProvider",
    "PROVIDER_REGISTRY",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUsageLimitError",
    "SummaryProvider",
    "SummaryResult",
    "build_provider_chain",
    "get_breaker",
    "parse_provider_entry",
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

    `complete` performs the raw backend call and returns the unvalidated LLM
    output (raising `ProviderError` for transport / HTTP / empty failures).
    `summarize` is `validate_and_canonicalize(self.complete(prompt))` and
    raises `ProviderError` for any unrecoverable response shape. Map-reduce
    (`scribe.pipeline.summary_map_reduce`) drives `complete` directly so an
    intermediate chunk summary is not forced through frontmatter validation.
    """

    name: str

    def complete(self, prompt: str) -> str: ...

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
    instructions: str | None = None,
    transcript: str | None = None,
) -> SummaryResult:
    """Try each provider in order, consulting its circuit breaker first.

    Tripped providers are skipped without a call and logged as
    `scribe.summary.provider_skipped_tripped`. Trip-relevant failures
    (`ProviderUsageLimitError`, `ProviderUnavailableError`,
    `ProviderTimeoutError`) and the generic `ProviderError` are caught and the
    chain advances; any other exception (auth, runtime error) propagates
    immediately.

    When `transcript` is supplied and the built `prompt` exceeds
    `settings.summary_map_reduce_chars`, each provider is driven via map-reduce
    (`scribe.pipeline.summary_map_reduce`) instead of a single call, so a
    payload-limited backend summarises the transcript in chunks rather than
    returning 413 (#382). `instructions` is the rendered prompt template (no
    transcript) reused for the reduce pass. Short prompts — or callers that do
    not pass `transcript` — keep the unchanged single-pass behaviour.

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

    use_map_reduce = (
        transcript is not None
        and default_settings.summary_map_reduce_chars > 0
        and len(prompt) > default_settings.summary_map_reduce_chars
    )

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
            if use_map_reduce:
                result = map_reduce_summarize(
                    provider,
                    instructions=instructions or "",
                    transcript=transcript or "",
                )
            else:
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


def _acquire_flock_bounded(
    lock_fd: int, timeout_secs: float, *, poll_interval: float = 0.1
) -> tuple[bool, float]:
    """Acquire an exclusive `flock`, blocking at most `timeout_secs`.

    Returns `(acquired, waited_secs)`. Polls non-blockingly (`LOCK_NB`) so a
    long-running codex held by another worker bounds the wait instead of
    blocking for the full codex timeout. `timeout_secs <= 0` means a single
    non-blocking attempt. `waited_secs` is recorded even on timeout so the
    caller can publish the contention metric.
    """
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True, time.monotonic() - start
        except OSError:
            # EAGAIN/EACCES: the lock is held by another process/fd.
            waited = time.monotonic() - start
            if waited >= timeout_secs:
                return False, waited
            time.sleep(min(poll_interval, max(0.0, timeout_secs - waited)))


class CodexProvider:
    """codex CLI backend. Serialised via `fcntl.flock` on
    `settings.codex_lock_path` so concurrent codex runs do not race the
    single-use OAuth refresh token.

    The lock spans the **whole** `codex exec`, not just an auth handshake: codex
    may rotate its single-use ChatGPT OAuth refresh token at an unpredictable
    point during the run and writes the rotated token back to the shared auth
    store. `codex exec` exposes no separate auth-only phase, and we run no codex
    daemon/pool, so the critical section cannot be narrowed below the full exec
    without risking mutual token revocation between concurrent workers.

    To stop that lock from globally serialising all summary work (issue #352),
    acquisition is **bounded** by `settings.codex_lock_wait_timeout_secs`: a
    worker that cannot get the lock in time raises `ProviderError`
    (`codex_lock_timeout`) so the fallback chain advances to the next provider
    rather than blocking for the full codex timeout. The lock-wait time is
    published to `metrics.codex_lock_wait_seconds` so contention is observable.
    `codex_lock_timeout` is a generic `ProviderError` (not trip-relevant): codex
    itself is healthy, just busy, so it must not trip codex's circuit breaker.

    Maps codex stderr signatures into typed `ProviderError` subclasses:
      * token_revoked / refresh_token_reused / "Please log out and sign in
        again" → `ProviderUnavailableError` (caller may also fire the operator
        Telegram alert after the whole chain fails).
      * "usage limit" / "rate limit" / "quota exceeded" → `ProviderUsageLimitError`.
      * subprocess timeout → `ProviderTimeoutError`.
      * Any other non-zero return → generic `ProviderError`.
    """

    name = "codex"

    def __init__(
        self, settings_obj: Settings | None = None, *, model: str | None = None
    ) -> None:
        self._settings = settings_obj or default_settings
        # Per-chain model override (#388, `codex:<model>`); None → use the
        # configured `codex_model` (which itself may be empty = codex config.toml).
        self._model = model
        self.last_token_revoked_stderr: str | None = None

    def summarize(self, prompt: str) -> SummaryResult:
        return validate_and_canonicalize(self.complete(prompt))

    def complete(self, prompt: str) -> str:
        s = self._settings
        lock_path = Path(s.codex_lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            acquired, waited = _acquire_flock_bounded(
                lock_fd, s.codex_lock_wait_timeout_secs
            )
            metrics.codex_lock_wait_seconds.observe(waited)
            if not acquired:
                log.warning(
                    "scribe.summary.codex_lock_timeout",
                    extra={
                        "waited_secs": round(waited, 2),
                        "timeout_secs": s.codex_lock_wait_timeout_secs,
                    },
                )
                raise ProviderError(
                    reason="codex_lock_timeout",
                    details=(
                        f"codex lock held by another summary for >"
                        f"{s.codex_lock_wait_timeout_secs}s; advancing to next provider"
                    ),
                )
            try:
                with tempfile.TemporaryDirectory(prefix="scribe-codex-") as tmp:
                    out_file = Path(tmp) / "summary.md"
                    cmd = [
                        s.codex_bin, "exec",
                        "--skip-git-repo-check",
                        "--dangerously-bypass-approvals-and-sandbox",
                        "-c", f"model_reasoning_effort={s.codex_reasoning}",
                        "-o", str(out_file),
                    ]
                    model = self._model or s.codex_model
                    if model:
                        cmd += ["-m", model]
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
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

        if not summary_md:
            raise ProviderError(reason="empty_response", details="codex produced empty output")
        metrics.last_codex_success_timestamp.set(time.time())
        return summary_md


class ClaudeProvider:
    """Claude CLI backend. Invokes `claude --model <m> --effort <e> -p <prompt>`
    non-interactively. Detects usage-limit / unavailable stderr substrings and
    maps them to the matching `ProviderError` subclass.
    """

    name = "claude"

    def __init__(
        self, settings_obj: Settings | None = None, *, model: str | None = None
    ) -> None:
        self._settings = settings_obj or default_settings
        # Per-chain model override (#388, `claude:<model>`); None → claude_model.
        self._model = model

    def summarize(self, prompt: str) -> SummaryResult:
        return validate_and_canonicalize(self.complete(prompt))

    def complete(self, prompt: str) -> str:
        s = self._settings
        # Pass the prompt via stdin (-p enables non-interactive print mode and
        # reads from stdin when no positional prompt is given). Avoids hitting
        # the kernel argv size limit (E2BIG) for long transcripts.
        cmd = [
            s.claude_bin,
            "--model", self._model or s.claude_model,
            "--effort", s.claude_effort,
            "-p",
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
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
        except OSError as exc:
            # E2BIG (argument list too long), ENOMEM, etc. — keep them inside
            # the fallback chain instead of bubbling out as raw OSError.
            raise ProviderError(
                reason="claude_exec_failed",
                details=f"{type(exc).__name__}: {exc}",
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
        return summary_md


class OpenAICompatibleProvider:
    """Generic OpenAI-compatible `/chat/completions` HTTP backend (#388).

    The single lightweight primitive behind every direct-HTTP summary provider:
    a summary is one prompt → markdown, so no agentic CLI harness is needed.
    POSTs to `${base_url}/chat/completions` with optional bearer auth and returns
    the first choice's content. Each instance carries its own
    `name`/`base_url`/`api_key`/`model`/`timeout`, so the same backend type can
    appear several times in the chain with different models (e.g.
    `ollama-cloud:glm-5.2` then `ollama-cloud:gemma4:31b`).

    Error mapping is identical for every instance, so the circuit breaker and
    `_classify_error` work uniformly:
      * empty `base_url` → `ProviderUnavailableError` (chain advances)
      * empty `api_key` when `require_api_key` → `ProviderUnavailableError`
      * httpx timeout → `ProviderTimeoutError`
      * other httpx transport error → `ProviderUnavailableError`
      * 429 → `ProviderUsageLimitError`
      * 5xx → `ProviderUnavailableError`
      * any other ≥400 (incl. 413 payload-too-large) → generic `ProviderError`
        — the chain advances; oversized inputs are handled upstream by map-reduce
        (#382), so a 413 should not normally be reached on the single-pass path.
      * unparseable / empty body → generic `ProviderError`

    The circuit breaker is keyed on `name` (the backend identity), not the model:
    trip-relevant failures (429/5xx/timeout) are backend-wide, so two models on
    the same backend correctly share one breaker, while model-specific errors
    (e.g. a 404 for a decommissioned model) are non-trip-relevant and simply
    advance to the next chain entry.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        require_api_key: bool = True,
    ) -> None:
        self.name = name
        self._base_url = (base_url or "").strip()
        self._api_key = (api_key or "").strip()
        self._model = model
        self._timeout = timeout
        self._require_api_key = require_api_key

    def summarize(self, prompt: str) -> SummaryResult:
        return validate_and_canonicalize(self.complete(prompt))

    def complete(self, prompt: str) -> str:
        if not self._base_url:
            raise ProviderUnavailableError(
                reason=f"{self.name}_no_base_url",
                details=f"base URL not configured for {self.name}",
            )
        if self._require_api_key and not self._api_key:
            raise ProviderUnavailableError(
                reason=f"{self.name}_no_api_key",
                details=f"API key not configured for {self.name}",
            )
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = httpx.post(
                url,
                content=json.dumps(body),
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                reason="timeout",
                details=f"{self.name} timed out after {self._timeout}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                reason=f"{self.name}_transport_error",
                details=str(exc),
            ) from exc

        if resp.status_code == 429:
            raise ProviderUsageLimitError(
                reason=f"{self.name}_usage_limit",
                details=resp.text[:400],
            )
        if 500 <= resp.status_code < 600:
            raise ProviderUnavailableError(
                reason=f"{self.name}_5xx",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )
        if resp.status_code >= 400:
            raise ProviderError(
                reason=f"{self.name}_http_error",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )

        try:
            data: dict[str, Any] = resp.json()
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                reason=f"{self.name}_bad_response",
                details=f"could not parse choices[0].message.content: {exc}",
            ) from exc

        summary_md = (content or "").strip()
        if not summary_md:
            raise ProviderError(
                reason="empty_response",
                details=f"{self.name} returned empty content",
            )
        return summary_md


class FreeLLMAPIProvider(OpenAICompatibleProvider):
    """`freellmapi` instance of `OpenAICompatibleProvider`.

    Convenience subclass that wires the `freellmapi_*` settings; the free
    aggregator requires a bearer key, so `require_api_key=True` (a missing key
    yields `ProviderUnavailableError(freellmapi_no_api_key)` and the chain
    advances).
    """

    def __init__(
        self, settings_obj: Settings | None = None, *, model: str | None = None
    ) -> None:
        s = settings_obj or default_settings
        super().__init__(
            name="freellmapi",
            base_url=s.freellmapi_base_url,
            api_key=s.freellmapi_api_key,
            model=model or s.freellmapi_model,
            timeout=s.freellmapi_timeout_secs,
            require_api_key=True,
        )


class OllamaCloudProvider(OpenAICompatibleProvider):
    """`ollama-cloud` instance of `OpenAICompatibleProvider` (#388).

    Wires the `ollama_*` settings. A local signed-in Ollama daemon needs no API
    key, so `require_api_key=False`; an unconfigured `ollama_base_url` still
    yields `ProviderUnavailableError(ollama-cloud_no_base_url)` so the chain
    advances rather than crashing.
    """

    def __init__(
        self, settings_obj: Settings | None = None, *, model: str | None = None
    ) -> None:
        s = settings_obj or default_settings
        super().__init__(
            name="ollama-cloud",
            base_url=s.ollama_base_url,
            api_key=s.ollama_api_key,
            model=model or s.ollama_model,
            timeout=s.ollama_timeout_secs,
            require_api_key=False,
        )


# A factory builds one provider instance from settings + an optional per-chain
# model override (parsed from a `provider:model` entry). Keeping factories rather
# than bare classes lets one backend type (OpenAICompatibleProvider) be
# registered under several provider names with their own settings wiring.
ProviderFactory = Callable[[Settings, "str | None"], SummaryProvider]


def _build_codex(s: Settings, model: str | None) -> SummaryProvider:
    return CodexProvider(s, model=model)


def _build_claude(s: Settings, model: str | None) -> SummaryProvider:
    return ClaudeProvider(s, model=model)


def _build_freellmapi(s: Settings, model: str | None) -> SummaryProvider:
    return FreeLLMAPIProvider(s, model=model)


def _build_ollama_cloud(s: Settings, model: str | None) -> SummaryProvider:
    return OllamaCloudProvider(s, model=model)


PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    "codex": _build_codex,
    "claude": _build_claude,
    "freellmapi": _build_freellmapi,
    "ollama-cloud": _build_ollama_cloud,
}


def parse_provider_entry(entry: str) -> tuple[str, str | None]:
    """Split a `provider[:model]` chain entry into `(provider_name, model)`.

    The entry is split on the FIRST `:` only, so a model tag that itself
    contains a colon (e.g. `ollama-cloud:gemma4:31b`) parses as
    provider=`ollama-cloud`, model=`gemma4:31b`. The provider name is lowercased
    (case-insensitive match against `PROVIDER_REGISTRY`); the model is kept
    verbatim because model identifiers are case-sensitive. A bare name with no
    `:` returns `model=None`, meaning "use the provider's default model" — this
    is the backward-compatible old name-only format.
    """
    name, sep, model = entry.partition(":")
    name = name.strip().lower()
    model = model.strip() if sep else ""
    return name, (model or None)


def build_provider_chain(
    settings_obj: Settings | None = None,
) -> list[SummaryProvider]:
    """Instantiate the configured provider chain from `summary_providers`.

    Each entry is a `provider[:model]` string (see `parse_provider_entry`).
    Unknown provider names raise `ValueError` rather than being silently dropped
    — a typo in env should surface loudly during process start, not be buried in
    a fallback path.
    """
    s = settings_obj or default_settings
    entries: list[str] = list(s.summary_providers) if s.summary_providers else []
    parsed = [parse_provider_entry(e) for e in entries]
    unknown = [name for name, _ in parsed if name not in PROVIDER_REGISTRY]
    if unknown:
        raise ValueError(
            "unknown summary providers in SCRIBE_SUMMARY_PROVIDERS: "
            + ", ".join(unknown)
            + f". Known providers: {sorted(PROVIDER_REGISTRY)}"
        )
    return [PROVIDER_REGISTRY[name](s, model) for name, model in parsed]
