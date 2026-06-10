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
) -> dict:
    return {
        "id": offer_id,
        "gpu_name": gpu_name,
        "dph_total": price,
        "cuda_max_good": cuda,
        "reliability": reliability,
        "inet_down": inet_down,
    }


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


# --- transport retry (transient scp/ssh drop) -------------------------------
import subprocess as _subprocess  # noqa: E402


def _cp(returncode, stderr=""):
    return _subprocess.CompletedProcess(["scp"], returncode, "", stderr)


def test_is_transient_transport_classification():
    assert whisper_client._is_transient_transport(_cp(124)) is True
    assert whisper_client._is_transient_transport(_cp(255, "Connection closed by remote host")) is True
    assert whisper_client._is_transient_transport(_cp(1, "lost connection")) is True
    assert whisper_client._is_transient_transport(_cp(0)) is False
    assert whisper_client._is_transient_transport(_cp(1, "scp: /x: No such file or directory")) is False


def test_scp_from_retries_transient_then_succeeds(monkeypatch, tmp_path):
    calls = []
    seq = [_cp(1, "lost connection"), _cp(0)]

    def fake_run(cmd, *, check=False, timeout=None):
        calls.append(cmd)
        return seq[len(calls) - 1]

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    monkeypatch.setattr(whisper_client.time, "sleep", lambda *_a, **_k: None)
    key = tmp_path / "id"
    key.write_text("k", encoding="utf-8")
    whisper_client._scp_from("h", 22, key, "/root/out/result.json", tmp_path / "out.json", attempts=3)
    assert len(calls) == 2  # failed once (transient), retried, succeeded


def test_scp_to_raises_after_exhausting_attempts(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, *, check=False, timeout=None):
        calls.append(cmd)
        return _cp(255, "Connection closed by remote host")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    monkeypatch.setattr(whisper_client.time, "sleep", lambda *_a, **_k: None)
    key = tmp_path / "id"
    key.write_text("k", encoding="utf-8")
    src = tmp_path / "src"
    src.write_text("data", encoding="utf-8")
    with pytest.raises(whisper_client.WhisperError, match="after 3 attempt"):
        whisper_client._scp_to("h", 22, key, src, "/root/work/x", attempts=3)
    assert len(calls) == 3  # exhausted all attempts


def test_scp_non_transient_fails_immediately_without_retry(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, *, check=False, timeout=None):
        calls.append(cmd)
        return _cp(1, "scp: /root/out/result.json: No such file or directory")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    monkeypatch.setattr(whisper_client.time, "sleep", lambda *_a, **_k: None)
    key = tmp_path / "id"
    key.write_text("k", encoding="utf-8")
    with pytest.raises(whisper_client.WhisperError):
        whisper_client._scp_from("h", 22, key, "/root/out/result.json", tmp_path / "out.json", attempts=3)
    assert len(calls) == 1  # non-transient: no retry
