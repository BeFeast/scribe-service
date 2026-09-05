"""Unit coverage for whisper_client offer selection + race recovery (#254)."""
from __future__ import annotations

import pytest

from scribe.config import settings
from scribe.pipeline import whisper_client
from scribe.pipeline.whisper_client import (
    WhisperError,
    _is_no_such_ask,
    _select_offers,
)


def _offer(
    offer_id: int,
    gpu_name: str,
    *,
    price: float = 0.5,
    cuda: float = 12.8,
    reliability: float = 0.99,
    inet_down: float = 1000.0,
    gpu_frac: float | None = None,
) -> dict:
    offer = {
        "id": offer_id,
        "gpu_name": gpu_name,
        "dph_total": price,
        "cuda_max_good": cuda,
        "reliability": reliability,
        "inet_down": inet_down,
    }
    if gpu_frac is not None:
        offer["gpu_frac"] = gpu_frac
    return offer


_BROADENED_FIXTURE_OFFERS = [
    _offer(1, "RTX A4000", price=0.45),
    _offer(2, "A100 PCIE", price=0.669, reliability=0.99),
    _offer(3, "RTX 3090", price=0.30),
    _offer(4, "RTX 5060 Ti", price=0.406),
    _offer(5, "RTX 5080", price=0.55),
    _offer(6, "A100 SXM4", price=0.735),
    _offer(7, "H100 SXM5", price=2.20),
    _offer(8, "RTX 2080"),  # filtered out by regex
    _offer(9, "RTX 3090", price=4.5),  # filtered out by price (cap 3.0)
    _offer(10, "RTX 3090", cuda=11.8),  # filtered out by min_cuda
    _offer(11, "RTX 3090", reliability=0.5),  # filtered out by reliability
]


def test_default_gpu_regex_matches_cards_seen_in_prod_outage(monkeypatch):
    """Live diagnosis from #254 found A100 PCIE / RTX 3090 / RTX 5060 Ti / RTX
    5080 / A100 SXM4 in the market; the previous regex matched only RTX A4000.
    The broadened default must accept all of them so a single momentary card
    outage no longer instantly fails every job."""
    captured: list[dict] = []

    def fake_vast(_api_key, _method, _path, _payload=None, timeout=60):
        captured.append({"timeout": timeout})
        return {"offers": list(_BROADENED_FIXTURE_OFFERS)}

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    candidates = _select_offers(
        "vast-test-key",
        max_price=settings.vast_max_price_per_hour,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=settings.vast_min_cuda,
    )

    matched_names = {offer["gpu_name"] for offer in candidates}
    assert {
        "RTX A4000",
        "A100 PCIE",
        "RTX 3090",
        "RTX 5060 Ti",
        "RTX 5080",
        "A100 SXM4",
        "H100 SXM5",
    } <= matched_names
    assert "RTX 2080" not in matched_names
    # Cheapest first (price), then reliability desc.
    prices = [float(offer["dph_total"]) for offer in candidates]
    assert prices == sorted(prices)


def test_select_offers_raises_with_clear_message_when_pool_is_empty(monkeypatch):
    monkeypatch.setattr(
        whisper_client,
        "_vast",
        lambda *_args, **_kwargs: {"offers": [_offer(1, "RTX 2080")]},
    )

    with pytest.raises(WhisperError, match="no Vast offer matched"):
        _select_offers(
            "vast-test-key",
            max_price=3.0,
            gpu_regex=r"\bRTX\s+4090\b",
            min_cuda=12.4,
        )


def test_select_offers_rejects_fractional_gpu_slices(monkeypatch):
    """#421: cheapest market offers are often gpu_frac=0.25 slices that still
    advertise full gpu_ram. Loading large-v3-turbo OOMs them and SSH drops
    mid-job. Only dedicated GPUs (gpu_frac≈1, or missing field) must match."""
    captured: list[dict] = []

    def fake_vast(_api_key, _method, _path, payload=None, timeout=60):
        captured.append(payload or {})
        return {
            "offers": [
                _offer(1, "RTX 5060 Ti", price=0.07, gpu_frac=0.25),
                _offer(2, "RTX 5060 Ti", price=0.08, gpu_frac=0.5),
                _offer(3, "RTX 5060 Ti", price=0.09, gpu_frac=1.0),
                _offer(4, "RTX A4000", price=0.10),  # missing gpu_frac → dedicated
            ],
        }

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)

    candidates = _select_offers(
        "vast-test-key",
        max_price=3.0,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=12.4,
    )

    assert [offer["id"] for offer in candidates] == [3, 4]
    assert captured and captured[0].get("gpu_frac") == {"eq": 1.0}


def test_select_offers_respects_caller_overrides(monkeypatch):
    """A test override of max_price/min_cuda/regex must be honored even when
    settings would have allowed a different offer."""
    monkeypatch.setattr(
        whisper_client,
        "_vast",
        lambda *_args, **_kwargs: {
            "offers": [
                _offer(1, "RTX 3090", price=2.5),
                _offer(2, "RTX 3090", price=0.5),
                _offer(3, "RTX 4090", price=0.6),
            ],
        },
    )

    candidates = _select_offers(
        "vast-test-key",
        max_price=1.0,
        gpu_regex=r"\bRTX\s+3090\b",
        min_cuda=12.4,
    )

    assert [offer["id"] for offer in candidates] == [2]


def test_is_no_such_ask_detects_offer_race():
    raced = WhisperError(
        "Vast API PUT /asks/12345/: HTTP 400: "
        "{\"error\":\"no_such_ask\",\"msg\":\"ask is not available\"}"
    )
    other = WhisperError("Vast API PUT /asks/12345/: HTTP 500: server exploded")
    assert _is_no_such_ask(raced) is True
    assert _is_no_such_ask(other) is False
    assert _is_no_such_ask(RuntimeError("HTTP 200 ok")) is False


def _stub_run_context_dependencies(monkeypatch, tmp_path):
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 30)
    monkeypatch.setattr(whisper_client, "_ensure_local_ssh_key", lambda: (key_path, "ssh-ed25519 test"))
    monkeypatch.setattr(whisper_client, "_ensure_vast_ssh_key", lambda *_args, **_kwargs: None)
    return key_path


def test_transcribe_impl_skips_vanished_offer_without_consuming_attempt(monkeypatch, tmp_path):
    """A `no_such_ask` race must immediately advance to the next candidate
    without spending the ready-timeout budget or burning an attempt slot. The
    second offer ultimately fails to become ready, so the test asserts both
    offers were tried even though `vast_offer_attempts=1`."""
    _stub_run_context_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "vast_offer_attempts", 1)
    sleeps: list[float] = []
    monkeypatch.setattr(whisper_client, "_sleep", sleeps.append)

    fixture_offers = [
        _offer(101, "RTX 3090", price=0.30),  # vanishes
        _offer(102, "RTX 3090", price=0.31),  # ready-wait fails
    ]
    monkeypatch.setattr(
        whisper_client,
        "_select_offers",
        lambda *_args, **_kwargs: list(fixture_offers),
    )

    create_calls: list[int] = []

    def fake_create(_api_key, offer, _public_key):
        create_calls.append(int(offer["id"]))
        if offer["id"] == 101:
            raise WhisperError(
                "Vast API PUT /asks/101/: HTTP 400: "
                "{\"error\":\"no_such_ask\",\"msg\":\"ask is not available\"}"
            )
        return 9001

    monkeypatch.setattr(whisper_client, "_create_instance", fake_create)

    destroyed: list[int] = []
    monkeypatch.setattr(
        whisper_client,
        "_destroy_instance",
        lambda _api_key, instance_id: destroyed.append(instance_id),
    )
    monkeypatch.setattr(
        whisper_client,
        "_wait_for_ssh",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(WhisperError("ssh never came up")),
    )

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="no Vast instance became ready"):
        whisper_client.transcribe(
            wav,
            title="race video",
            source_url="https://youtu.be/race-video",
        )

    assert create_calls == [101, 102]
    assert destroyed == [9001]
    # The vanished branch paces the create endpoint (#426).
    assert sleeps == [1.0]


def test_transcribe_impl_reads_tunables_from_settings(monkeypatch, tmp_path):
    """`max_price_per_hour`, `min_cuda`, `gpu_regex` and `offer_attempts` must
    all flow from settings down into `_select_offers` and the attempt loop."""
    _stub_run_context_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "vast_max_price_per_hour", 2.5)
    monkeypatch.setattr(settings, "vast_min_cuda", 12.6)
    monkeypatch.setattr(settings, "vast_gpu_regex", r"\bH100\b")
    monkeypatch.setattr(settings, "vast_offer_attempts", 4)

    seen: dict[str, object] = {}

    def fake_select(_api_key, *, max_price, gpu_regex, min_cuda):
        seen["max_price"] = max_price
        seen["gpu_regex"] = gpu_regex
        seen["min_cuda"] = min_cuda
        return [_offer(1, "H100 SXM5", price=2.0)]

    monkeypatch.setattr(whisper_client, "_select_offers", fake_select)
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_a, **_k: 5005)
    monkeypatch.setattr(
        whisper_client,
        "_wait_for_ssh",
        lambda *_a, **_k: (_ for _ in ()).throw(WhisperError("never ready")),
    )
    monkeypatch.setattr(whisper_client, "_destroy_instance", lambda *_a, **_k: None)

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="no Vast instance became ready"):
        whisper_client.transcribe(
            wav,
            title="tunable video",
            source_url="https://youtu.be/tunable",
        )

    assert seen == {"max_price": 2.5, "gpu_regex": r"\bH100\b", "min_cuda": 12.6}


def test_transcribe_impl_invokes_monthly_cap_check_before_provisioning(monkeypatch, tmp_path):
    """The cap callback must run before we hit Vast for offers; if it raises,
    the API key is never consulted and no instance is requested."""
    _stub_run_context_dependencies(monkeypatch, tmp_path)
    select_calls: list[bool] = []
    create_calls: list[bool] = []
    monkeypatch.setattr(
        whisper_client,
        "_select_offers",
        lambda *_a, **_k: select_calls.append(True) or [],
    )
    monkeypatch.setattr(
        whisper_client,
        "_create_instance",
        lambda *_a, **_k: create_calls.append(True) or 1,
    )

    def cap() -> None:
        raise WhisperError("monthly cap reached")

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="monthly cap reached"):
        whisper_client.transcribe(
            wav,
            title="cap video",
            source_url="https://youtu.be/cap",
            check_monthly_cap=cap,
        )

    assert select_calls == []
    assert create_calls == []


# --- Vast API 429 retry/backoff (#426) -----------------------------------


def _http_error(code: int, body: str, headers: dict | None = None):
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "https://console.vast.ai/api/v0/test/",
        code,
        "err",
        headers or {},
        io.BytesIO(body.encode("utf-8")),
    )


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._body


def _patch_retry_pacing(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(whisper_client, "_sleep", sleeps.append)
    monkeypatch.setattr(whisper_client, "_jitter", lambda: 0.0)
    return sleeps


def test_vast_api_request_retries_429_then_succeeds(monkeypatch):
    """One transient per-endpoint 429 (job 491's killer) must be absorbed by
    bounded backoff instead of surfacing as WhisperError."""
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(timeout)
        if len(calls) < 3:
            raise _http_error(429, "API requests too frequent: endpoint threshold=2.0*1.0")
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/1/") == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [2.5, 5.0]


def test_vast_api_request_exhausts_retries_and_raises(monkeypatch):
    sleeps = _patch_retry_pacing(monkeypatch)

    def fake_urlopen(_req, timeout=45):
        raise _http_error(429, "API requests too frequent")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    # Typed post-retry 429 so the provider layer can classify rate-limit
    # pressure without substring-matching aggregated error text.
    with pytest.raises(whisper_client.VastRateLimitedError, match="HTTP 429"):
        whisper_client.vast_api_request("k", "GET", "/instances/1/")
    assert sleeps == [2.5, 5.0, 10.0]


def test_vast_api_request_honors_retry_after_body_hint(monkeypatch):
    """Vast sends no Retry-After header, but the 429 JSON body sometimes
    carries `retry_after` (observed 10s/17s in production) — honor it when it
    exceeds the computed backoff."""
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, '{"detail": "too frequent", "retry_after": 17}')
        return _FakeResponse(b"{}")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/1/") == {}
    assert sleeps == [17.0]


def test_vast_api_request_does_not_retry_client_errors(monkeypatch):
    """4xx (except 408/429) must fail fast — e.g. the no_such_ask offer race
    relies on the immediate HTTP 400 surfacing to the offer loop."""
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        raise _http_error(400, '{"error": "no_such_ask", "msg": "ask is not available"}')

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    with pytest.raises(WhisperError, match="HTTP 400"):
        whisper_client.vast_api_request("k", "PUT", "/asks/1/", {})
    assert len(calls) == 1
    assert sleeps == []


def test_vast_api_request_retries_network_errors_as_whisper_error(monkeypatch):
    import urllib.error

    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) < 2:
            raise urllib.error.URLError("connection reset")
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/") == {"ok": True}
    assert sleeps == [2.5]


def test_transcribe_returns_result_when_final_destroy_fails(monkeypatch, tmp_path):
    """#426 D2: a rate-limited teardown after a successful transcription must
    not discard the finished (paid) transcript — the destroy sweep + reaper
    own the cleanup retry via the on_destroy_failed bookkeeping."""
    import subprocess

    _stub_run_context_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(
        whisper_client, "_select_offers", lambda *_a, **_k: [_offer(1, "RTX 3090", price=0.3)]
    )
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_a, **_k: 4242)
    monkeypatch.setattr(whisper_client, "_wait_for_ssh", lambda *_a, **_k: ("gpu.example", 22))
    monkeypatch.setattr(whisper_client, "_wait_remote_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        whisper_client, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(whisper_client, "_scp_to", lambda *_a, **_k: None)

    def fake_scp_from(_host, _port, _key, src, target, **_kwargs):
        if str(src).endswith("result.json"):
            target.write_text(
                '{"detected_language": "en", "duration_seconds": 12.5, "backend": "fake"}',
                encoding="utf-8",
            )
        else:
            target.write_text("# transcript", encoding="utf-8")

    monkeypatch.setattr(whisper_client, "_scp_from", fake_scp_from)

    destroy_attempts: list[int] = []

    def failing_destroy(_api_key, instance_id):
        destroy_attempts.append(instance_id)
        raise WhisperError("Vast API GET /instances/4242/: HTTP 429: API requests too frequent")

    monkeypatch.setattr(whisper_client, "_destroy_instance", failing_destroy)

    failed_callback: list[int] = []
    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    result = whisper_client.transcribe(
        wav,
        title="teardown 429 video",
        source_url="https://youtu.be/teardown-429",
        on_destroy_failed=failed_callback.append,
    )

    assert result.transcript_md == "# transcript"
    assert result.vast_instance_id == 4242
    assert destroy_attempts == [4242]
    assert failed_callback == [4242]


def test_vast_api_request_honors_retry_after_header(monkeypatch):
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, "too frequent", headers={"Retry-After": "12"})
        return _FakeResponse(b"{}")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/1/") == {}
    assert sleeps == [12.0]


def test_vast_api_request_clamps_absurd_retry_hints(monkeypatch):
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, '{"retry_after": 3600}')
        return _FakeResponse(b"{}")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/1/") == {}
    assert sleeps == [whisper_client._VAST_RETRY_MAX_SLEEP_SECONDS]


def test_vast_api_request_falls_back_when_hint_is_garbage(monkeypatch):
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(
                429, '{"retry_after": "soon"}', headers={"Retry-After": "later"}
            )
        return _FakeResponse(b"{}")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client.vast_api_request("k", "GET", "/instances/1/") == {}
    assert sleeps == [2.5]


def test_create_instance_does_not_retry_ambiguous_failures(monkeypatch):
    """PUT /asks/ is non-idempotent and starts billing server-side: a 502 or
    network failure is ambiguous (the create may have landed), so it must be
    sent exactly once and fail this offer instead of double-creating."""
    import urllib.error

    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen_502(_req, timeout=45):
        calls.append(1)
        raise _http_error(502, "bad gateway")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen_502)
    with pytest.raises(WhisperError, match="HTTP 502"):
        whisper_client._create_instance("k", _offer(1, "RTX 3090"), "ssh-ed25519 test")
    assert len(calls) == 1
    assert sleeps == []

    calls.clear()

    def fake_urlopen_neterr(_req, timeout=45):
        calls.append(1)
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen_neterr)
    with pytest.raises(WhisperError, match="connection reset"):
        whisper_client._create_instance("k", _offer(1, "RTX 3090"), "ssh-ed25519 test")
    assert len(calls) == 1
    assert sleeps == []


def test_create_instance_still_retries_429(monkeypatch):
    """A 429 on the create is rejected by Vast's limiter before processing,
    so resending is safe — and required, or one collision kills the offer."""
    sleeps = _patch_retry_pacing(monkeypatch)
    calls: list[int] = []

    def fake_urlopen(_req, timeout=45):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, "too frequent")
        return _FakeResponse(b'{"new_contract": 9009}')

    monkeypatch.setattr(whisper_client, "_urlopen", fake_urlopen)

    assert whisper_client._create_instance("k", _offer(1, "RTX 3090"), "key") == 9009
    # create (429), create (ok), then the best-effort ssh-key attach POST
    assert len(calls) == 3
    assert sleeps == [2.5]


def test_transcribe_impl_caps_vanished_offer_churn(monkeypatch, tmp_path):
    """#426: a contended market must not machine-gun PUT /asks/ across the
    whole candidate list — the vanished-offer path stops after
    3 x vast_offer_attempts."""
    _stub_run_context_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "vast_offer_attempts", 1)
    monkeypatch.setattr(whisper_client, "_sleep", lambda _s: None)

    fixture_offers = [_offer(200 + i, "RTX 3090", price=0.3) for i in range(10)]
    monkeypatch.setattr(
        whisper_client, "_select_offers", lambda *_a, **_k: list(fixture_offers)
    )

    create_calls: list[int] = []

    def fake_create(_api_key, offer, _public_key):
        create_calls.append(int(offer["id"]))
        raise WhisperError(
            f"Vast API PUT /asks/{offer['id']}/: HTTP 400: "
            '{"error":"no_such_ask","msg":"ask is not available"}'
        )

    monkeypatch.setattr(whisper_client, "_create_instance", fake_create)

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="no Vast instance became ready"):
        whisper_client.transcribe(
            wav, title="churn video", source_url="https://youtu.be/churn"
        )

    # vanished cap = offer_attempts * 3 = 3 creates, not all 10 candidates
    assert len(create_calls) == 3
