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

The codex backend currently lives in `scribe.pipeline.summarizer.summarize`;
this module defines the shared contract additional backends will plug into.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Protocol, runtime_checkable

from scribe.config import settings
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
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUsageLimitError",
    "SummaryProvider",
    "SummaryResult",
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
                window_secs=settings.summary_breaker_window_secs,
                threshold=settings.summary_breaker_threshold,
                cooldown_secs=settings.summary_breaker_cooldown_secs,
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
    providers: list[SummaryProvider], prompt: str
) -> SummaryResult:
    """Try each provider in order, consulting its circuit breaker first.

    Tripped providers are skipped without a call and logged as
    `scribe.summary.provider_skipped_tripped`. Trip-relevant failures
    (`ProviderUsageLimitError`, `ProviderUnavailableError`,
    `ProviderTimeoutError`) and the generic `ProviderError` are caught and the
    chain advances; any other exception (auth, runtime error) propagates
    immediately.

    Raises `ProviderError(reason="chain_exhausted")` if every provider failed
    or was skipped, or `ProviderError(reason="no_providers")` if the chain is
    empty.
    """
    if not providers:
        raise ProviderError(reason="no_providers", details="empty provider chain")

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
                "summary provider %s failed (%s): %s",
                name,
                outcome,
                exc.details or exc.reason,
            )
            last_error = exc
            had_fallback = True
            continue

        metrics.summary_provider_calls_total.labels(
            provider=name, result="success"
        ).inc()
        breaker.record("success", mode=mode)
        _publish_state(breaker)

        chain_label = "success_after_fallback" if had_fallback else "success_first"
        metrics.summary_chain_outcome_total.labels(outcome=chain_label).inc()
        return result

    metrics.summary_chain_outcome_total.labels(outcome="all_failed").inc()
    if last_error is None:
        raise ProviderError(
            reason="chain_exhausted",
            details="all providers skipped (tripped)",
        )
    raise ProviderError(
        reason="chain_exhausted",
        details=f"all providers failed; last={last_error.reason}: {last_error.details}",
    )
