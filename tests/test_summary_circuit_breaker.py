"""Tests for the per-provider circuit breaker + Prometheus metrics that wrap
the summary fallback chain (see scribe.pipeline.summary_providers).

The tests target the breaker primitive directly (deterministic, no real LLM
calls) and the `summarize_with_chain` integration that consumes it.
"""
from __future__ import annotations

import threading

import pytest

from scribe.obs import metrics
from scribe.pipeline import summary_providers
from scribe.pipeline.summary_providers import CircuitBreaker
from scribe.pipeline.summary_validator import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsageLimitError,
    SummaryResult,
)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    summary_providers._reset_breakers_for_test()
    yield
    summary_providers._reset_breakers_for_test()


class _Clock:
    """Tiny patchable clock so tests can fast-forward past the cooldown."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def advance(self, secs: float) -> None:
        self.now += secs

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(summary_providers, "_now", c)
    return c


class _FakeProvider:
    """Provider that replays a scripted sequence of results/exceptions."""

    def __init__(self, name: str, script: list[object]) -> None:
        self.name = name
        self.script: list[object] = list(script)
        self.calls = 0

    def summarize(self, prompt: str) -> SummaryResult:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        assert isinstance(item, SummaryResult)
        return item


def _ok_result() -> SummaryResult:
    return SummaryResult(
        summary_md="---\ntags: [t]\n---\n\nbody\n",
        tags=["t"],
        short_description="x",
    )


def _counter_value(
    counter, **labels: str
) -> float:
    """Read the current value of a labelled Counter via the public collect()."""
    label_names = sorted(labels.keys())
    for metric in counter.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_total"):
                continue
            if all(sample.labels.get(k) == labels[k] for k in label_names):
                return float(sample.value)
    return 0.0


def _gauge_value(gauge, **labels: str) -> float:
    for metric in gauge.collect():
        for sample in metric.samples:
            if all(sample.labels.get(k) == labels[k] for k in labels):
                return float(sample.value)
    return 0.0


# ---------- CircuitBreaker primitive ------------------------------------------


def test_three_consecutive_trip_relevant_failures_trip_breaker(clock: _Clock) -> None:
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )

    for _ in range(3):
        mode = breaker.acquire()
        assert mode == "allow"
        breaker.record("usage_limit", mode=mode)

    assert breaker.state == "tripped"
    assert breaker.acquire() == "skip"


def test_only_last_threshold_outcomes_count(clock: _Clock) -> None:
    """A success in between resets the trip streak (deque keeps only last N)."""
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )

    # fail, fail, success, fail, fail → last 3 = [success, fail, fail] → no trip
    for outcome in ("timeout", "unavailable"):
        mode = breaker.acquire()
        breaker.record(outcome, mode=mode)

    mode = breaker.acquire()
    breaker.record("success", mode=mode)

    for outcome in ("timeout", "unavailable"):
        mode = breaker.acquire()
        breaker.record(outcome, mode=mode)

    assert breaker.state == "closed"


def test_old_failures_outside_window_do_not_trip(clock: _Clock) -> None:
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )

    mode = breaker.acquire()
    breaker.record("usage_limit", mode=mode)
    mode = breaker.acquire()
    breaker.record("usage_limit", mode=mode)

    # Two failures, then jump past the window before the third.
    clock.advance(400)
    mode = breaker.acquire()
    breaker.record("usage_limit", mode=mode)

    # Last 3 outcomes are all trip-relevant, but the first two are outside
    # the window → no trip.
    assert breaker.state == "closed"


def test_generic_error_outcome_is_not_trip_relevant(clock: _Clock) -> None:
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )
    for _ in range(5):
        mode = breaker.acquire()
        breaker.record("error", mode=mode)
    assert breaker.state == "closed"


def test_half_open_trial_success_closes_breaker(clock: _Clock) -> None:
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )

    # Trip the breaker.
    for _ in range(3):
        mode = breaker.acquire()
        breaker.record("usage_limit", mode=mode)
    assert breaker.state == "tripped"

    clock.advance(601)
    mode = breaker.acquire()
    assert mode == "trial"
    assert breaker.state == "half_open"

    breaker.record("success", mode=mode)
    assert breaker.state == "closed"
    assert breaker.acquire() == "allow"


def test_half_open_trial_failure_restarts_cooldown(clock: _Clock) -> None:
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )
    for _ in range(3):
        mode = breaker.acquire()
        breaker.record("usage_limit", mode=mode)

    clock.advance(601)
    mode = breaker.acquire()
    assert mode == "trial"

    breaker.record("usage_limit", mode=mode)
    assert breaker.state == "tripped"
    # cooldown was restarted; still tripped after the same interval.
    clock.advance(599)
    assert breaker.acquire() == "skip"


def test_half_open_concurrent_callers_only_one_runs_trial(
    clock: _Clock,
) -> None:
    """While the trial call is in flight, other threads must see `skip`."""
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )
    for _ in range(3):
        mode = breaker.acquire()
        breaker.record("usage_limit", mode=mode)
    clock.advance(601)

    # First caller wins the trial; the second sees half_open with the trial
    # in progress and must skip.
    first = breaker.acquire()
    second = breaker.acquire()
    assert first == "trial"
    assert second == "skip"

    # After the trial resolves, normal traffic flows again.
    breaker.record("success", mode=first)
    assert breaker.acquire() == "allow"


def test_half_open_lock_under_real_thread_race(clock: _Clock) -> None:
    """Spin up two threads racing through `acquire` once the cooldown elapses;
    exactly one must get the trial, the other must skip."""
    breaker = CircuitBreaker(
        "codex", window_secs=300, threshold=3, cooldown_secs=600
    )
    for _ in range(3):
        mode = breaker.acquire()
        breaker.record("usage_limit", mode=mode)
    clock.advance(601)

    barrier = threading.Barrier(2)
    results: list[str] = []
    results_lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        outcome = breaker.acquire()
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert sorted(results) == ["skip", "trial"]


# ---------- summarize_with_chain integration ----------------------------------


def test_chain_trips_codex_after_three_usage_limits_then_skips_it(
    clock: _Clock,
) -> None:
    codex = _FakeProvider(
        "codex",
        script=[
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            # would succeed, but the breaker should keep this call from
            # running on the 4th invocation.
            _ok_result(),
        ],
    )
    fallback = _FakeProvider("claude", script=[_ok_result()] * 4)

    # First three runs: codex raises usage-limit, claude succeeds.
    for _ in range(3):
        result = summary_providers.summarize_with_chain([codex, fallback], "p")
        assert isinstance(result, SummaryResult)
    assert codex.calls == 3

    skipped_before = _counter_value(
        metrics.summary_provider_calls_total,
        provider="codex",
        result="skipped_tripped",
    )

    # Fourth run: codex is tripped, gets skipped without a call.
    result = summary_providers.summarize_with_chain([codex, fallback], "p")
    assert isinstance(result, SummaryResult)
    assert codex.calls == 3  # unchanged

    skipped_after = _counter_value(
        metrics.summary_provider_calls_total,
        provider="codex",
        result="skipped_tripped",
    )
    assert skipped_after == skipped_before + 1
    assert _gauge_value(metrics.summary_provider_state, provider="codex") == 2


def test_chain_counters_increment_per_outcome(clock: _Clock) -> None:
    timeout_provider = _FakeProvider(
        "p_timeout",
        script=[ProviderTimeoutError(reason="timeout")],
    )
    unavailable_provider = _FakeProvider(
        "p_unavailable",
        script=[ProviderUnavailableError(reason="unavailable")],
    )
    generic_provider = _FakeProvider(
        "p_generic",
        script=[ProviderError(reason="shape_invalid")],
    )
    happy_provider = _FakeProvider("p_ok", script=[_ok_result()])

    before = {
        "timeout": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_timeout",
            result="timeout",
        ),
        "unavailable": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_unavailable",
            result="unavailable",
        ),
        "error": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_generic",
            result="error",
        ),
        "success": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_ok",
            result="success",
        ),
        "after_fallback": _counter_value(
            metrics.summary_chain_outcome_total,
            outcome="success_after_fallback",
        ),
    }

    result = summary_providers.summarize_with_chain(
        [timeout_provider, unavailable_provider, generic_provider, happy_provider],
        "p",
    )
    assert isinstance(result, SummaryResult)

    after = {
        "timeout": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_timeout",
            result="timeout",
        ),
        "unavailable": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_unavailable",
            result="unavailable",
        ),
        "error": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_generic",
            result="error",
        ),
        "success": _counter_value(
            metrics.summary_provider_calls_total,
            provider="p_ok",
            result="success",
        ),
        "after_fallback": _counter_value(
            metrics.summary_chain_outcome_total,
            outcome="success_after_fallback",
        ),
    }
    for key, value in before.items():
        assert after[key] == value + 1, f"{key}: expected {value}+1, got {after[key]}"


def test_chain_success_first_label_when_initial_provider_wins() -> None:
    provider = _FakeProvider("p", script=[_ok_result()])
    before = _counter_value(
        metrics.summary_chain_outcome_total, outcome="success_first"
    )
    summary_providers.summarize_with_chain([provider], "p")
    after = _counter_value(
        metrics.summary_chain_outcome_total, outcome="success_first"
    )
    assert after == before + 1


def test_chain_all_failed_label_when_every_provider_errors() -> None:
    bad = _FakeProvider(
        "bad",
        script=[ProviderError(reason="shape_invalid")],
    )
    before = _counter_value(
        metrics.summary_chain_outcome_total, outcome="all_failed"
    )
    with pytest.raises(ProviderError):
        summary_providers.summarize_with_chain([bad], "p")
    after = _counter_value(
        metrics.summary_chain_outcome_total, outcome="all_failed"
    )
    assert after == before + 1


def test_chain_trial_success_clears_breaker(clock: _Clock) -> None:
    codex = _FakeProvider(
        "codex",
        script=[
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            _ok_result(),  # trial call succeeds
            _ok_result(),  # subsequent normal call
        ],
    )

    for _ in range(3):
        with pytest.raises(ProviderError):
            summary_providers.summarize_with_chain([codex], "p")
    assert codex.calls == 3
    assert summary_providers.get_breaker("codex").state == "tripped"

    clock.advance(601)
    result = summary_providers.summarize_with_chain([codex], "p")
    assert isinstance(result, SummaryResult)
    assert codex.calls == 4
    assert summary_providers.get_breaker("codex").state == "closed"

    # Subsequent call uses the now-closed breaker normally.
    result = summary_providers.summarize_with_chain([codex], "p")
    assert isinstance(result, SummaryResult)
    assert codex.calls == 5


def test_chain_trial_failure_re_trips_breaker(clock: _Clock) -> None:
    codex = _FakeProvider(
        "codex",
        script=[
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),  # trial fails
        ],
    )
    for _ in range(3):
        with pytest.raises(ProviderError):
            summary_providers.summarize_with_chain([codex], "p")
    assert summary_providers.get_breaker("codex").state == "tripped"

    clock.advance(601)
    with pytest.raises(ProviderError):
        summary_providers.summarize_with_chain([codex], "p")
    assert codex.calls == 4
    assert summary_providers.get_breaker("codex").state == "tripped"


def test_get_breaker_returns_same_instance_per_name() -> None:
    a = summary_providers.get_breaker("codex")
    b = summary_providers.get_breaker("codex")
    assert a is b
    c = summary_providers.get_breaker("claude")
    assert c is not a


def test_state_gauge_reflects_transitions(clock: _Clock) -> None:
    codex = _FakeProvider(
        "codex",
        script=[
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
            ProviderUsageLimitError(reason="usage_limit"),
        ],
    )
    for _ in range(3):
        with pytest.raises(ProviderError):
            summary_providers.summarize_with_chain([codex], "p")
    assert _gauge_value(metrics.summary_provider_state, provider="codex") == 2

    clock.advance(601)
    # Touch the breaker: a chain run will mark it half_open at acquire time
    # then promote it back to tripped on the failing trial.
    codex.script = [ProviderUsageLimitError(reason="usage_limit")]
    with pytest.raises(ProviderError):
        summary_providers.summarize_with_chain([codex], "p")
    assert _gauge_value(metrics.summary_provider_state, provider="codex") == 2


def test_settings_defaults_match_issue_spec() -> None:
    from scribe.config import settings

    assert settings.summary_breaker_window_secs == 300
    assert settings.summary_breaker_threshold == 3
    assert settings.summary_breaker_cooldown_secs == 600
