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


def _fake_job(callback_url: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=42, callback_url=callback_url)


def _fake_jobview() -> JobView:
    return JobView(
        job_id=42, url="https://youtu.be/x" * 1, video_id="dQw4w9WgXcQ",
        status="done", error=None, deduplicated=False, callback_url=None, transcript=None,
    )


def test_deliver_webhook_skipped_when_no_callback(monkeypatch):
    """Empty/None callback_url → counter('skipped'), never touches the net."""
    counts: dict[str, int] = {}
    def fake_labels(outcome):
        class C:
            def inc(self):
                counts[outcome] = counts.get(outcome, 0) + 1
        return C()
    monkeypatch.setattr(loop_module.metrics, "webhook_deliveries_total",
                        SimpleNamespace(labels=fake_labels))
    loop_module._deliver_webhook(_fake_session(), _fake_job(None))
    assert counts == {"skipped": 1}


def test_deliver_webhook_malformed_url_does_not_raise(monkeypatch):
    """A `not-a-url` callback_url makes urllib.request.Request() raise
    ValueError. The Codex P2 fix pulled Request() inside the try and added
    ValueError to the net_error except — together they preserve the
    "never raises" contract that the worker's post-processing depends on."""
    counts: dict[str, int] = {}
    def fake_labels(outcome):
        class C:
            def inc(self):
                counts[outcome] = counts.get(outcome, 0) + 1
        return C()
    monkeypatch.setattr(loop_module.metrics, "webhook_deliveries_total",
                        SimpleNamespace(labels=fake_labels))
    monkeypatch.setattr(loop_module, "render_job_view",
                        lambda session, job: _fake_jobview())

    # Must not raise — must record net_error.
    loop_module._deliver_webhook(_fake_session(), _fake_job("not-a-url"))
    assert counts == {"net_error": 1}


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


def test_webhook_latency_histogram_buckets():
    """SCR-13 #9: buckets must match the acceptance criterion exactly.
    prometheus_client appends a final +Inf bucket; drop it before comparing."""
    assert list(metrics.webhook_delivery_latency_seconds._upper_bounds[:-1]) == [
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

    def boom(*a, **kw):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(loop_module.urllib.request, "urlopen", boom)

    _, before_count = _latency_sample()
    loop_module._deliver_webhook(_fake_session(), _fake_job("http://example.test/hook"))
    _, after_count = _latency_sample()
    assert after_count == before_count
