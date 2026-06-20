"""Tests for the transcription provider chain + per-provider circuit breaker
(see scribe.pipeline.transcribe_providers).

Mirrors tests/test_summary_circuit_breaker.py: the chain machinery and breaker
transitions are exercised with scripted fake providers (no real GPU / network),
plus focused unit tests for the concrete VastProvider / OpenAITranscribeProvider
/ LocalWhisperProvider backends.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scribe.config import Settings
from scribe.obs import metrics
from scribe.pipeline import summary_providers, transcribe_providers
from scribe.pipeline.transcribe_providers import (
    LocalWhisperProvider,
    OpenAITranscribeProvider,
    TranscribeChainError,
    TranscribeProviderError,
    TranscribeProviderTimeoutError,
    TranscribeProviderUnavailableError,
    TranscribeProviderUsageLimitError,
    TranscribeRequest,
    VastProvider,
    build_provider_chain,
    transcribe_with_chain,
)
from scribe.pipeline.whisper_client import (
    TranscribeResult,
    TranscribeTimeoutError,
    WhisperError,
)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    transcribe_providers._reset_breakers_for_test()
    yield
    transcribe_providers._reset_breakers_for_test()


class _Clock:
    """Patchable monotonic clock so tests can fast-forward past the cooldown.

    The reused CircuitBreaker reads time via summary_providers._now, so the
    clock is installed there.
    """

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


def _result(provider: str = "vast", *, cost: float = 0.0) -> TranscribeResult:
    return TranscribeResult(
        transcript_md="# t\n\n## Transcript\n\nhi\n",
        detected_language="en",
        duration_seconds=12.0,
        backend="fake",
        vast_instance_id=0,
        vast_cost=cost,
        provider=provider,
    )


class _FakeProvider:
    """Replays a scripted sequence of results / exceptions."""

    def __init__(self, name: str, script: list[object]) -> None:
        self.name = name
        self.script: list[object] = list(script)
        self.calls = 0

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        assert isinstance(item, TranscribeResult)
        return item


def _req(tmp_path: Path | None = None) -> TranscribeRequest:
    wav = (tmp_path or Path("/tmp")) / "input-16k.wav"
    return TranscribeRequest(wav=wav, title="t", source_url="https://example/x")


def _counter_value(counter, **labels: str) -> float:
    for metric in counter.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_total"):
                continue
            if all(sample.labels.get(k) == labels[k] for k in labels):
                return float(sample.value)
    return 0.0


def _gauge_value(gauge, **labels: str) -> float:
    for metric in gauge.collect():
        for sample in metric.samples:
            if all(sample.labels.get(k) == labels[k] for k in labels):
                return float(sample.value)
    return 0.0


# ---------- chain: Vast-failure → fallback (AC1) ------------------------------


def test_vast_failure_falls_through_to_fallback_and_records_provider() -> None:
    vast = _FakeProvider(
        "vast",
        script=[TranscribeProviderUnavailableError(reason="vast_unavailable")],
    )
    fallback = _FakeProvider("openai", script=[_result("openai", cost=0.02)])

    result = transcribe_with_chain([vast, fallback], _req())

    assert vast.calls == 1
    assert fallback.calls == 1
    # The chain stamps the serving provider on the result (provider recorded).
    assert result.provider == "openai"


def test_chain_stamps_serving_provider_even_if_result_disagrees() -> None:
    # Provider returns a result mislabelled "vast"; the chain overrides it with
    # the provider's own name so the recorded provider is always authoritative.
    fallback = _FakeProvider("openai", script=[_result("vast")])
    result = transcribe_with_chain([fallback], _req())
    assert result.provider == "openai"


def test_wallclock_timeout_propagates_and_is_not_a_fallthrough() -> None:
    vast = _FakeProvider(
        "vast", script=[TranscribeTimeoutError("transcribe timed out after 5s")]
    )
    fallback = _FakeProvider("openai", script=[_result("openai")])
    with pytest.raises(TranscribeTimeoutError):
        transcribe_with_chain([vast, fallback], _req())
    # Fallback must NOT run once the whole-job budget is blown.
    assert fallback.calls == 0


def test_empty_chain_raises_no_providers() -> None:
    with pytest.raises(TranscribeChainError) as exc:
        transcribe_with_chain([], _req())
    assert exc.value.reason == "no_providers"


def test_all_providers_failed_raises_chain_exhausted() -> None:
    before = _counter_value(metrics.transcribe_chain_outcome_total, outcome="all_failed")
    bad = _FakeProvider(
        "vast", script=[TranscribeProviderUnavailableError(reason="vast_unavailable")]
    )
    with pytest.raises(TranscribeChainError) as exc:
        transcribe_with_chain([bad], _req())
    assert exc.value.reason == "chain_exhausted"
    after = _counter_value(metrics.transcribe_chain_outcome_total, outcome="all_failed")
    assert after == before + 1


def test_chain_outcome_labels(clock: _Clock) -> None:
    first = _counter_value(metrics.transcribe_chain_outcome_total, outcome="success_first")
    transcribe_with_chain([_FakeProvider("vast", [_result("vast")])], _req())
    assert _counter_value(
        metrics.transcribe_chain_outcome_total, outcome="success_first"
    ) == first + 1

    after_fb = _counter_value(
        metrics.transcribe_chain_outcome_total, outcome="success_after_fallback"
    )
    transcribe_with_chain(
        [
            _FakeProvider("vast", [TranscribeProviderUnavailableError(reason="x")]),
            _FakeProvider("openai", [_result("openai")]),
        ],
        _req(),
    )
    assert _counter_value(
        metrics.transcribe_chain_outcome_total, outcome="success_after_fallback"
    ) == after_fb + 1


# ---------- breaker open / close transitions (AC4) ----------------------------


def test_breaker_trips_vast_after_threshold_then_skips_it(clock: _Clock) -> None:
    """Default threshold is 2: after two consecutive Vast failures the breaker
    trips and the third run skips Vast without a call, serving the fallback."""
    assert Settings().transcribe_breaker_threshold == 2
    vast = _FakeProvider(
        "vast",
        script=[
            TranscribeProviderUnavailableError(reason="vast_unavailable"),
            TranscribeProviderUnavailableError(reason="vast_unavailable"),
        ],
    )
    fallback = _FakeProvider("openai", script=[_result("openai")] * 5)

    for _ in range(2):
        transcribe_with_chain([vast, fallback], _req())
    assert vast.calls == 2
    assert transcribe_providers.get_breaker("vast").state == "tripped"
    assert _gauge_value(metrics.transcribe_provider_state, provider="vast") == 2

    skipped_before = _counter_value(
        metrics.transcribe_provider_calls_total, provider="vast", result="skipped_tripped"
    )
    result = transcribe_with_chain([vast, fallback], _req())
    assert result.provider == "openai"
    assert vast.calls == 2  # vast was skipped, not called
    skipped_after = _counter_value(
        metrics.transcribe_provider_calls_total, provider="vast", result="skipped_tripped"
    )
    assert skipped_after == skipped_before + 1


def test_breaker_half_open_trial_success_closes_and_serves_vast_again(
    clock: _Clock,
) -> None:
    vast = _FakeProvider(
        "vast",
        script=[
            TranscribeProviderUnavailableError(reason="vast_unavailable"),
            TranscribeProviderUnavailableError(reason="vast_unavailable"),
            _result("vast"),  # trial after cooldown
            _result("vast"),  # normal call once closed
        ],
    )
    fallback = _FakeProvider("openai", script=[_result("openai")] * 5)

    for _ in range(2):
        transcribe_with_chain([vast, fallback], _req())
    assert transcribe_providers.get_breaker("vast").state == "tripped"

    # Skipped while tripped.
    transcribe_with_chain([vast, fallback], _req())
    assert vast.calls == 2

    clock.advance(601)
    result = transcribe_with_chain([vast, fallback], _req())
    assert result.provider == "vast"  # trial ran and won
    assert vast.calls == 3
    assert transcribe_providers.get_breaker("vast").state == "closed"

    result = transcribe_with_chain([vast, fallback], _req())
    assert result.provider == "vast"
    assert vast.calls == 4


def test_breaker_half_open_trial_failure_re_trips(clock: _Clock) -> None:
    vast = _FakeProvider(
        "vast",
        script=[TranscribeProviderUnavailableError(reason="vast_unavailable")] * 3,
    )
    fallback = _FakeProvider("openai", script=[_result("openai")] * 5)

    for _ in range(2):
        transcribe_with_chain([vast, fallback], _req())
    assert transcribe_providers.get_breaker("vast").state == "tripped"

    clock.advance(601)
    transcribe_with_chain([vast, fallback], _req())  # trial fails
    assert vast.calls == 3
    assert transcribe_providers.get_breaker("vast").state == "tripped"


# ---------- build_provider_chain ----------------------------------------------


def test_build_provider_chain_default_is_vast_only(monkeypatch) -> None:
    monkeypatch.delenv("SCRIBE_TRANSCRIBE_PROVIDERS", raising=False)
    assert Settings().transcribe_providers == ["vast"]
    chain = build_provider_chain(Settings())
    assert [p.name for p in chain] == ["vast"]
    assert isinstance(chain[0], VastProvider)


def test_build_provider_chain_returns_configured_order() -> None:
    s = Settings()
    s.transcribe_providers = ["vast", "openai", "local-whisper"]
    chain = build_provider_chain(s)
    assert [p.name for p in chain] == ["vast", "openai", "local-whisper"]


def test_build_provider_chain_rejects_unknown_provider() -> None:
    s = Settings()
    s.transcribe_providers = ["vast", "bogus"]
    with pytest.raises(ValueError, match="bogus"):
        build_provider_chain(s)


def test_build_provider_chain_forwards_callbacks_only_to_vast() -> None:
    s = Settings()
    s.transcribe_providers = ["vast", "openai"]
    created: list[int] = []
    cb = created.append
    chain = build_provider_chain(s, on_instance_created=cb)
    vast = chain[0]
    assert isinstance(vast, VastProvider)
    assert vast._on_instance_created is cb
    # The hosted provider does not receive Vast lifecycle callbacks.
    assert isinstance(chain[1], OpenAITranscribeProvider)


# ---------- VastProvider translation ------------------------------------------


def test_vast_provider_translates_whisper_error(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise WhisperError("no Vast instance became ready; last error: ...")

    monkeypatch.setattr(transcribe_providers.whisper_client, "transcribe", boom)
    with pytest.raises(TranscribeProviderUnavailableError) as exc:
        VastProvider().transcribe(_req())
    assert exc.value.reason == "vast_unavailable"
    assert "no Vast instance" in exc.value.details


def test_vast_provider_propagates_wallclock_timeout(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise TranscribeTimeoutError("transcribe timed out after 1800s")

    monkeypatch.setattr(transcribe_providers.whisper_client, "transcribe", boom)
    with pytest.raises(TranscribeTimeoutError):
        VastProvider().transcribe(_req())


def test_vast_provider_forwards_lifecycle_callbacks(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_transcribe(wav, **kwargs):
        seen.update(kwargs)
        return _result("vast", cost=0.01)

    monkeypatch.setattr(
        transcribe_providers.whisper_client, "transcribe", fake_transcribe
    )
    cb = lambda _i: None  # noqa: E731
    provider = VastProvider(on_instance_created=cb, check_monthly_cap=lambda: None)
    result = provider.transcribe(_req())
    assert result.vast_cost == 0.01
    assert seen["on_instance_created"] is cb
    assert callable(seen["check_monthly_cap"])


# ---------- OpenAITranscribeProvider ------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _openai_settings(**overrides) -> Settings:
    s = Settings()
    s.openai_transcribe_api_key = "test-key"
    s.openai_transcribe_max_job_cost_usd = 0.50
    s.openai_transcribe_cost_per_minute_usd = 0.006
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_openai_unavailable_without_api_key() -> None:
    s = Settings()
    s.openai_transcribe_api_key = ""
    with pytest.raises(TranscribeProviderUnavailableError) as exc:
        OpenAITranscribeProvider(s).transcribe(_req())
    assert exc.value.reason == "openai_no_api_key"


def test_openai_success_builds_result_and_cost(monkeypatch, tmp_path) -> None:
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"RIFFfake")
    posted: dict = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted["data"] = kwargs.get("data")
        return _FakeResp(
            200,
            payload={"text": "hello world", "language": "en", "duration": 120.0},
        )

    monkeypatch.setattr(transcribe_providers.httpx, "post", fake_post)
    req = TranscribeRequest(wav=wav, title="My Title", source_url="https://e/x")
    result = OpenAITranscribeProvider(_openai_settings()).transcribe(req)

    assert result.provider == "openai"
    assert "hello world" in result.transcript_md
    assert "# My Title" in result.transcript_md
    assert result.detected_language == "en"
    # 120s = 2 min * $0.006 = $0.012
    assert result.vast_cost == pytest.approx(0.012)
    assert posted["url"].endswith("/audio/transcriptions")


def test_openai_cost_cap_rejects_before_upload(monkeypatch, tmp_path) -> None:
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"RIFFfake")
    called = {"post": False}

    def fake_post(*_a, **_k):
        called["post"] = True
        return _FakeResp(200, payload={"text": "x"})

    monkeypatch.setattr(transcribe_providers.httpx, "post", fake_post)
    # 2h of audio at $0.006/min = $0.72 > $0.50 cap.
    req = TranscribeRequest(
        wav=wav, title="t", source_url="https://e/x", duration_seconds=7200
    )
    with pytest.raises(TranscribeProviderError) as exc:
        OpenAITranscribeProvider(_openai_settings()).transcribe(req)
    assert exc.value.reason == "openai_cost_cap"
    assert called["post"] is False  # no money spent


def test_openai_429_is_usage_limit(monkeypatch, tmp_path) -> None:
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"x")
    monkeypatch.setattr(
        transcribe_providers.httpx, "post", lambda *_a, **_k: _FakeResp(429, text="slow down")
    )
    with pytest.raises(TranscribeProviderUsageLimitError):
        OpenAITranscribeProvider(_openai_settings()).transcribe(_req(tmp_path))


def test_openai_5xx_is_unavailable(monkeypatch, tmp_path) -> None:
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"x")
    monkeypatch.setattr(
        transcribe_providers.httpx, "post", lambda *_a, **_k: _FakeResp(503, text="down")
    )
    with pytest.raises(TranscribeProviderUnavailableError):
        OpenAITranscribeProvider(_openai_settings()).transcribe(_req(tmp_path))


def test_openai_timeout_is_timeout(monkeypatch, tmp_path) -> None:
    import httpx as _httpx

    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"x")

    def boom(*_a, **_k):
        raise _httpx.TimeoutException("timed out")

    monkeypatch.setattr(transcribe_providers.httpx, "post", boom)
    with pytest.raises(TranscribeProviderTimeoutError):
        OpenAITranscribeProvider(_openai_settings()).transcribe(_req(tmp_path))


# ---------- LocalWhisperProvider ----------------------------------------------


def test_local_whisper_unavailable_when_dependency_missing(monkeypatch, tmp_path) -> None:
    # Force `from faster_whisper import WhisperModel` to raise ImportError,
    # regardless of whether the optional dependency is installed.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"x")
    with pytest.raises(TranscribeProviderUnavailableError) as exc:
        LocalWhisperProvider().transcribe(_req(tmp_path))
    assert exc.value.reason == "faster_whisper_missing"


# ---------- settings defaults -------------------------------------------------


def test_settings_defaults_match_issue_spec() -> None:
    s = Settings()
    assert s.transcribe_providers == ["vast"]
    assert s.transcribe_breaker_window_secs == 900
    assert s.transcribe_breaker_threshold == 2
    assert s.transcribe_breaker_cooldown_secs == 600
