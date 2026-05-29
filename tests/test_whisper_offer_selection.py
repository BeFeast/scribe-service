"""Tests for Vast offer selection + offer→ask race handling (#254).

The defaults here must stay broad enough that a thin Vast market does not
take down transcription. The settings overrides exercised below are the same
ones operators can flip via Infisical / `SCRIBE_VAST_*` env without a rebuild.
"""
from __future__ import annotations

import re

import pytest

from scribe.config import Settings, settings
from scribe.pipeline import whisper_client
from scribe.pipeline.whisper_client import (
    WhisperError,
    _is_offer_race,
    _select_offers,
)

_DEFAULT_GPU_REGEX = Settings.model_fields["vast_gpu_regex"].default


@pytest.mark.parametrize(
    "gpu_name",
    [
        # Previously-matched cards that must keep working.
        "RTX 4090",
        "RTX A4000",
        "RTX A5000",
        "RTX A6000",
        "A10",
        "A40",
        "L4",
        "L40",
        "L40S",
        "RTX 4000 Ada",
        "RTX 5000 Ada Generation",
        "RTX 6000 Ada Generation",
        # Cards added by #254 (live diagnosis pool that should not be excluded).
        "RTX 3090",
        "RTX 4080",
        "RTX 5060 Ti",
        "RTX 5070",
        "RTX 5080",
        "RTX 5090",
        "A100 PCIE",
        "A100 SXM4",
        "H100 PCIe",
        "H200",
    ],
)
def test_default_gpu_regex_matches_supported_card(gpu_name: str) -> None:
    pattern = re.compile(_DEFAULT_GPU_REGEX, re.IGNORECASE)
    assert pattern.search(gpu_name), f"default regex should match {gpu_name!r}"


@pytest.mark.parametrize(
    "gpu_name",
    [
        "GTX 1080 Ti",
        "RTX 2080",
        "RTX 3080",
        "Tesla T4",
    ],
)
def test_default_gpu_regex_rejects_unsupported_card(gpu_name: str) -> None:
    pattern = re.compile(_DEFAULT_GPU_REGEX, re.IGNORECASE)
    assert not pattern.search(gpu_name), f"default regex should reject {gpu_name!r}"


def test_select_offers_uses_settings_min_cuda_and_regex(monkeypatch) -> None:
    offers_payload = {
        "offers": [
            # A100 — must pass under the default regex, was excluded before #254.
            {"id": 1, "gpu_name": "A100 PCIE", "dph_total": 0.669,
             "cuda_max_good": 12.8, "reliability": 0.99},
            # RTX 3090 — was excluded before #254.
            {"id": 2, "gpu_name": "RTX 3090", "dph_total": 0.40,
             "cuda_max_good": 12.8, "reliability": 1.0},
            # Old-school RTX A4000 — kept for parity.
            {"id": 3, "gpu_name": "RTX A4000", "dph_total": 0.30,
             "cuda_max_good": 12.5, "reliability": 0.95},
            # cuda < settings.vast_min_cuda → filtered.
            {"id": 4, "gpu_name": "RTX 4090", "dph_total": 0.50,
             "cuda_max_good": 12.0, "reliability": 0.99},
            # reliability < 0.90 → filtered.
            {"id": 5, "gpu_name": "RTX 4090", "dph_total": 0.50,
             "cuda_max_good": 12.6, "reliability": 0.80},
            # gpu_name doesn't match regex → filtered.
            {"id": 6, "gpu_name": "GTX 1080 Ti", "dph_total": 0.20,
             "cuda_max_good": 12.6, "reliability": 0.99},
            # price > max_price → filtered.
            {"id": 7, "gpu_name": "H100", "dph_total": 1.50,
             "cuda_max_good": 12.8, "reliability": 0.99},
        ]
    }
    monkeypatch.setattr(
        whisper_client, "_vast",
        lambda _key, _method, _path, _payload, timeout=60: offers_payload,
    )

    candidates = _select_offers("fixture-key", max_price=1.0)
    ids = [int(c["id"]) for c in candidates]
    # Sorted by (price asc, reliability desc) → A4000 (0.30), 3090 (0.40), A100 (0.669).
    assert ids == [3, 2, 1]


def test_select_offers_honours_runtime_regex_override(monkeypatch) -> None:
    """A tightened SCRIBE_VAST_GPU_REGEX must restrict the candidate pool."""
    offers_payload = {
        "offers": [
            {"id": 10, "gpu_name": "A100 PCIE", "dph_total": 0.6,
             "cuda_max_good": 12.8, "reliability": 0.99},
            {"id": 11, "gpu_name": "RTX 4090", "dph_total": 0.5,
             "cuda_max_good": 12.8, "reliability": 0.99},
        ]
    }
    monkeypatch.setattr(
        whisper_client, "_vast",
        lambda *_a, **_k: offers_payload,
    )
    monkeypatch.setattr(settings, "vast_gpu_regex", r"\bRTX\s+4090\b")

    candidates = _select_offers("fixture-key", max_price=1.0)
    assert [c["id"] for c in candidates] == [11]


def test_select_offers_raises_when_pool_empty(monkeypatch) -> None:
    offers_payload = {"offers": [{"id": 99, "gpu_name": "GTX 1080",
                                  "dph_total": 0.1, "cuda_max_good": 12.8,
                                  "reliability": 0.99}]}
    monkeypatch.setattr(
        whisper_client, "_vast",
        lambda *_a, **_k: offers_payload,
    )
    with pytest.raises(WhisperError, match="no Vast offer matched"):
        _select_offers("fixture-key", max_price=1.0)


def test_is_offer_race_detects_no_such_ask_400() -> None:
    exc = WhisperError(
        "Vast API PUT /asks/12345/: HTTP 400: "
        '{"detail":"no_such_ask: offer 12345 not available"}'
    )
    assert _is_offer_race(exc) is True


@pytest.mark.parametrize(
    "message",
    [
        # Different endpoint — not a race.
        "Vast API POST /instances/12345/ssh/: HTTP 400: bad",
        # Different status code on /asks/ — not a race.
        "Vast API PUT /asks/12345/: HTTP 500: server error",
        # 400 on /asks/ but unrelated payload.
        "Vast API PUT /asks/12345/: HTTP 400: bad_request",
        "transcribe timed out after 1800s",
    ],
)
def test_is_offer_race_rejects_other_failures(message: str) -> None:
    assert _is_offer_race(WhisperError(message)) is False


def test_transcribe_advances_past_offer_race_without_consuming_budget(
    monkeypatch, tmp_path,
) -> None:
    """When PUT /asks/{id}/ returns no_such_ask we must move on to the next
    offer immediately — no instance was created, so the ready-timeout budget
    is irrelevant."""
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")

    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 30)
    monkeypatch.setattr(settings, "vast_offer_attempts", 4)
    monkeypatch.setattr(
        whisper_client, "_ensure_local_ssh_key",
        lambda: (key_path, "ssh-ed25519 test"),
    )
    monkeypatch.setattr(
        whisper_client, "_ensure_vast_ssh_key", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        whisper_client, "_select_offers",
        lambda *_a, **_k: [
            {"id": 1001, "dph_total": 0.40},
            {"id": 1002, "dph_total": 0.45},
            {"id": 1003, "dph_total": 0.50},
        ],
    )

    create_calls: list[int] = []

    def fake_create_instance(_api_key, offer, _public_key):
        offer_id = int(offer["id"])
        create_calls.append(offer_id)
        if offer_id in (1001, 1002):
            raise WhisperError(
                f"Vast API PUT /asks/{offer_id}/: HTTP 400: no_such_ask: not available"
            )
        return 7777

    monkeypatch.setattr(whisper_client, "_create_instance", fake_create_instance)

    destroyed: list[int] = []
    monkeypatch.setattr(
        whisper_client, "_destroy_instance",
        lambda _api_key, instance_id: destroyed.append(instance_id),
    )
    monkeypatch.setattr(
        whisper_client, "_wait_for_ssh",
        lambda *_a, **_k: ("127.0.0.1", 22),
    )
    monkeypatch.setattr(whisper_client, "_wait_remote_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(whisper_client, "_scp_to", lambda *_a, **_k: None)
    monkeypatch.setattr(whisper_client, "_scp_from", lambda *_a, **_k: None)

    def fake_run(cmd, *, check=True, timeout=None):
        import subprocess

        if cmd[-1] == "mkdir -p /root/work /root/out" or cmd[-1].startswith("cd /root"):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(whisper_client, "_run", fake_run)

    result_json_path = tmp_path / "result.json"
    result_md_path = tmp_path / "transcript.md"

    def fake_scp_from(host, port, key, src, target):
        if src.endswith("result.json"):
            target.write_text('{"detected_language": "en", "duration_seconds": 1.0, "backend": "fake"}')
        else:
            target.write_text("# fake\n")

    monkeypatch.setattr(whisper_client, "_scp_from", fake_scp_from)

    wav = tmp_path / "audio.wav"
    wav.write_text("wav", encoding="utf-8")

    # Pre-bind the budget knobs so this test is deterministic regardless of
    # operator overrides in the live environment.
    monkeypatch.setattr(settings, "vast_instance_ready_timeout_secs", 600)

    result = whisper_client.transcribe(
        wav, title="race video", source_url="https://example.test/x",
    )

    assert create_calls == [1001, 1002, 1003]
    # First two races never produced an instance → no destroy expected for them.
    # The successful third instance is destroyed via the finally block.
    assert destroyed == [7777]
    assert result.vast_instance_id == 7777
    _ = result_json_path  # appease lint: variables are written via _scp_from
    _ = result_md_path
