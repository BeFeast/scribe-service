"""Pure unit tests for worker._deliver_webhook safety contract.

The function is documented as "never raises": failures land in
scribe_webhook_deliveries_total + the log, the worker keeps going.
A bad callback_url that makes urllib.request.Request() raise ValueError
used to escape this contract and mark an otherwise-successful job as
failed (PR #6 Codex P2). These tests pin both arms."""
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

from scribe.api.schemas import JobView
from scribe.obs import metrics
from scribe.worker import loop as loop_module


def _fake_session() -> SimpleNamespace:
    """A session that the route's render_job_view doesn't actually need
    because we monkeypatch render_job_view directly."""
    return SimpleNamespace()


def _fake_job(callback_url: str | None, correlation_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=42, callback_url=callback_url, correlation_id=correlation_id)


def _fake_jobview() -> JobView:
    return JobView(
        job_id=42, url="https://youtu.be/x" * 1, video_id="dQw4w9WgXcQ",
        status="done", error=None, deduplicated=False, callback_url=None, transcript=None,
    )


def _patch_webhook_counters(monkeypatch) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {"deliveries": {}, "attempts": {}}

    def fake_counter(metric: str):
        def fake_labels(outcome):
            class C:
                def inc(self):
                    bucket = counts[metric]
                    bucket[outcome] = bucket.get(outcome, 0) + 1
            return C()
        return SimpleNamespace(labels=fake_labels)

    monkeypatch.setattr(
        loop_module.metrics,
        "webhook_deliveries_total",
        fake_counter("deliveries"),
    )
    monkeypatch.setattr(
        loop_module.metrics,
        "webhook_attempts_total",
        fake_counter("attempts"),
    )
    return counts


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return b""


def _within_jitter_band(actual: float, base_s: float) -> bool:
    """Assert a webhook retry sleep fell within the documented ±10% jitter band."""
    spread = base_s * loop_module._WEBHOOK_RETRY_JITTER
    return base_s - spread <= actual <= base_s + spread


def test_deliver_webhook_skipped_when_no_callback(monkeypatch):
    """Empty/None callback_url → counter('skipped'), never touches the net."""
    counts = _patch_webhook_counters(monkeypatch)
    loop_module._deliver_webhook(_fake_session(), _fake_job(None))
    assert counts == {"deliveries": {"skipped": 1}, "attempts": {}}


def test_deliver_webhook_skipped_when_notify_false(monkeypatch):
    """notify=False (#296) suppresses delivery even with a callback_url set —
    counted as 'skipped', never touches the net."""
    counts = _patch_webhook_counters(monkeypatch)
    job = _fake_job("https://example.com/hook")
    job.notify = False
    loop_module._deliver_webhook(_fake_session(), job)
    assert counts == {"deliveries": {"skipped": 1}, "attempts": {}}


def test_deliver_webhook_sends_correlation_id_header(monkeypatch):
    """A job with a correlation_id must surface it as X-Request-ID on the
    webhook POST so the receiver can stitch the trace (#357)."""
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    captured: dict[str, str] = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None
        def read(self):
            return b""

    def fake_urlopen(req, timeout):
        captured.update(req.headers)
        return FakeResp()

    monkeypatch.setattr(loop_module.urllib.request, "urlopen", fake_urlopen)
    loop_module._deliver_webhook(
        _fake_session(), _fake_job("https://example.com/hook", correlation_id="req-xyz")
    )
    assert captured.get("X-request-id") == "req-xyz"


def test_deliver_webhook_omits_correlation_id_header_when_unset(monkeypatch):
    """No correlation_id on the job → no X-Request-ID header on the POST."""
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    captured: dict[str, str] = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None
        def read(self):
            return b""

    def fake_urlopen(req, timeout):
        captured.update(req.headers)
        return FakeResp()

    monkeypatch.setattr(loop_module.urllib.request, "urlopen", fake_urlopen)
    loop_module._deliver_webhook(
        _fake_session(), _fake_job("https://example.com/hook", correlation_id=None)
    )
    assert "X-request-id" not in captured


def test_deliver_webhook_malformed_url_does_not_raise(monkeypatch):
    """A `not-a-url` callback_url makes urllib.request.Request() raise
    ValueError. It is deterministic bad input, so it is counted once and
    must not enter the retry backoff loop."""
    counts = _patch_webhook_counters(monkeypatch)
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())

    # Must not raise — must record net_error.
    loop_module._deliver_webhook(_fake_session(), _fake_job("not-a-url"))
    assert counts == {
        "deliveries": {"net_error": 1},
        "attempts": {"net_error": 1},
    }


def _latency_sample() -> tuple[float, float]:
    """Return (_sum, _count) of the webhook latency histogram from the
    exposition body so tests don't reach into prometheus_client internals."""
    body, _ = metrics.export()
    total = 0.0
    count = 0.0
    for line in body.decode().splitlines():
        if line.startswith("scribe_webhook_delivery_latency_seconds_sum"):
            total = float(line.split()[-1])
        elif line.startswith("scribe_webhook_delivery_latency_seconds_count"):
            count = float(line.split()[-1])
    return total, count


def _latency_buckets() -> list[float]:
    """Return finite bucket boundaries from the Prometheus exposition."""
    body, _ = metrics.export()
    buckets: list[float] = []
    for line in body.decode().splitlines():
        if not line.startswith("scribe_webhook_delivery_latency_seconds_bucket{"):
            continue
        le = line.split('le="', 1)[1].split('"', 1)[0]
        if le != "+Inf":
            buckets.append(float(le))
    return buckets


def test_webhook_latency_histogram_buckets():
    """SCR-13 #9: buckets must match the acceptance criterion exactly."""
    assert _latency_buckets() == [
        .05, .1, .25, .5, 1, 2.5, 5, 10,
    ]


def test_webhook_latency_observed_on_success(monkeypatch):
    """A successful urlopen must bump the latency histogram count."""
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None
        def read(self):
            return b""

    monkeypatch.setattr(loop_module.urllib.request, "urlopen",
                        lambda *a, **kw: FakeResp())

    _, before_count = _latency_sample()
    loop_module._deliver_webhook(_fake_session(), _fake_job("http://example.test/hook"))
    _, after_count = _latency_sample()
    assert after_count == before_count + 1


def test_webhook_latency_not_observed_on_http_error(monkeypatch):
    """Non-2xx replies must NOT increment the latency histogram."""
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    monkeypatch.setattr(loop_module.time, "sleep", lambda seconds: None)

    def boom(*a, **kw):
        raise urllib.error.HTTPError(
            "http://example.test/hook", 500, "boom", {}, None,
        )

    monkeypatch.setattr(loop_module.urllib.request, "urlopen", boom)

    _, before_count = _latency_sample()
    loop_module._deliver_webhook(_fake_session(), _fake_job("http://example.test/hook"))
    _, after_count = _latency_sample()
    assert after_count == before_count


def test_webhook_latency_not_observed_on_net_error(monkeypatch):
    """Network errors must NOT increment the latency histogram."""
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    monkeypatch.setattr(loop_module.time, "sleep", lambda seconds: None)

    def boom(*a, **kw):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(loop_module.urllib.request, "urlopen", boom)

    _, before_count = _latency_sample()
    loop_module._deliver_webhook(_fake_session(), _fake_job("http://example.test/hook"))
    _, after_count = _latency_sample()
    assert after_count == before_count


def test_deliver_webhook_retries_then_succeeds(monkeypatch):
    """A transient delivery failure is counted, backed off, and retried."""
    counts = _patch_webhook_counters(monkeypatch)
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        calls += 1
        assert timeout == loop_module._WEBHOOK_TIMEOUT_S
        if calls == 1:
            raise urllib.error.URLError("temporary")
        return _FakeResponse()

    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    monkeypatch.setattr(loop_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(loop_module.time, "sleep", sleeps.append)

    loop_module._deliver_webhook(_fake_session(), _fake_job("https://example.com/hook"))

    assert calls == 2
    # Backoff is jittered within ±10% of the 1.0s base.
    assert len(sleeps) == 1
    assert _within_jitter_band(sleeps[0], 1.0)
    assert counts == {
        "deliveries": {"ok": 1},
        "attempts": {"net_error": 1, "ok": 1},
    }


def test_deliver_webhook_retries_then_gives_up(monkeypatch):
    """Persistent HTTP failures stop after the 1s/4s/16s retry backoff."""
    counts = _patch_webhook_counters(monkeypatch)
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=503,
            msg="unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    monkeypatch.setattr(loop_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(loop_module.time, "sleep", sleeps.append)

    loop_module._deliver_webhook(_fake_session(), _fake_job("https://example.com/hook"))

    assert calls == 4
    # Each retry sleep is jittered within ±10% of its base (1.0/4.0/16.0).
    assert len(sleeps) == 3
    assert _within_jitter_band(sleeps[0], 1.0)
    assert _within_jitter_band(sleeps[1], 4.0)
    assert _within_jitter_band(sleeps[2], 16.0)
    assert counts == {
        "deliveries": {"http_error": 1},
        "attempts": {"http_error": 4},
    }


def test_deliver_webhook_does_not_retry_non_transient_4xx(monkeypatch):
    """Client errors other than 429 are terminal without retry backoff."""
    counts = _patch_webhook_counters(monkeypatch)
    sleeps: list[float] = []
    calls = 0

    def fake_urlopen(req, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=404,
            msg="not found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())
    monkeypatch.setattr(loop_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(loop_module.time, "sleep", sleeps.append)

    loop_module._deliver_webhook(_fake_session(), _fake_job("https://example.com/hook"))

    assert calls == 1
    assert sleeps == []
    assert counts == {
        "deliveries": {"http_error": 1},
        "attempts": {"http_error": 1},
    }


def test_jittered_backoff_stays_within_documented_band():
    """_jittered_backoff must stay within ±10% of the base and actually vary
    so concurrent deliveries decorrelate (issue #351)."""
    import random

    base = 4.0
    samples = [loop_module._jittered_backoff(base) for _ in range(200)]
    for s in samples:
        assert _within_jitter_band(s, base)
    # Jitter must produce more than one distinct value across a healthy sample.
    assert len(set(round(s, 6) for s in samples)) > 1
    # The mean of a symmetric ±10% band should sit close to the base, keeping
    # the total retry budget roughly equivalent to the fixed schedule.
    assert abs(sum(samples) / len(samples) - base) < base * 0.05
    # Deterministic seeding must still be possible for reproducible runs.
    random.seed(1234)
    a = loop_module._jittered_backoff(base)
    random.seed(1234)
    b = loop_module._jittered_backoff(base)
    assert a == b
