"""Tests for scr-271 fast-fail + per-job host blacklist + ready-timeout vs cost-budget."""
from __future__ import annotations

import time

import pytest

from scribe.config import settings
from scribe.pipeline import whisper_client
from scribe.pipeline.whisper_client import (
    VastInstanceFailedError,
    VastReadyTimeoutError,
    WhisperError,
    _ensure_budget,
    _select_offers,
    _vast_failure_state,
    _wait_for_ssh,
)


def _offer(
    offer_id: int,
    gpu_name: str = "RTX 3090",
    *,
    host_id: int | None = 100,
    price: float = 0.5,
    cuda: float = 12.8,
    reliability: float = 0.99,
    inet_down: float = 1000.0,
) -> dict:
    return {
        "id": offer_id,
        "host_id": host_id,
        "gpu_name": gpu_name,
        "dph_total": price,
        "cuda_max_good": cuda,
        "reliability": reliability,
        "inet_down": inet_down,
    }


# ---- _vast_failure_state ----------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"actual_status": "running", "cur_state": "running"}, None),
        ({"actual_status": "loading", "cur_state": "creating"}, None),
        ({"actual_status": "exited", "cur_state": "running"}, "exited"),
        ({"actual_status": "running", "cur_state": "failed"}, "failed"),
        ({"actual_status": "", "cur_state": "", "intended_status": "crashed"}, "crashed"),
        ({"actual_status": "offline"}, "offline"),
        ({"actual_status": "stopped"}, "stopped"),
        ({}, None),
    ],
)
def test_vast_failure_state_detects_terminal_states(info, expected):
    assert _vast_failure_state(info) == expected


# ---- _ensure_budget — distinguish ready_timeout from cost-cap ---------------


def test_ensure_budget_no_op_before_deadline():
    started = time.monotonic()
    _ensure_budget(started, started + 60.0, price=0.20, max_cost=0.25, ready_timeout=600.0)


def test_ensure_budget_ready_timeout_uses_distinct_exception(monkeypatch):
    started = time.monotonic() - 700.0  # 700s in the past
    deadline = time.monotonic() - 1.0  # already expired
    with pytest.raises(VastReadyTimeoutError, match="ready_timeout exceeded"):
        _ensure_budget(started, deadline, price=0.20, max_cost=0.25, ready_timeout=600.0, label="offer_id=42 host_id=99")


def test_ensure_budget_cost_cap_message_when_no_ready_timeout():
    """Without ready_timeout context, the existing cost-cap message wins."""
    started = time.monotonic() - 700.0
    deadline = time.monotonic() - 1.0
    with pytest.raises(WhisperError) as excinfo:
        _ensure_budget(started, deadline, price=0.20, max_cost=0.25)
    assert "budget guard tripped" in str(excinfo.value)
    assert not isinstance(excinfo.value, VastReadyTimeoutError)


def test_ensure_budget_label_appears_in_message():
    started = time.monotonic() - 700.0
    deadline = time.monotonic() - 1.0
    with pytest.raises(VastReadyTimeoutError, match=r"\(offer_id=7 host_id=42\)"):
        _ensure_budget(started, deadline, price=0.20, max_cost=0.25, ready_timeout=600.0, label="offer_id=7 host_id=42")


# ---- _wait_for_ssh — fast-fail on terminal-failure state --------------------


def test_wait_for_ssh_fast_fails_on_failed_actual_status(monkeypatch, tmp_path):
    """When the Vast container reaches actual_status=exited we must raise
    VastInstanceFailedError immediately, not poll for ready_timeout."""
    key = tmp_path / "id"
    key.write_text("k", encoding="utf-8")
    started = time.monotonic()
    deadline = started + 600.0

    monkeypatch.setattr(
        whisper_client,
        "_get_instance",
        lambda _api_key, _id: {
            "actual_status": "exited",
            "cur_state": "failed",
            "status_msg": "OCI runtime create failed: could not apply required module",
        },
    )

    # _run is unreachable on the failure path — the API check fires first.
    monkeypatch.setattr(
        whisper_client,
        "_run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ssh probe must not run")),
    )

    with pytest.raises(VastInstanceFailedError) as excinfo:
        _wait_for_ssh(
            "vast-test-key", 9999, key, started, deadline, price=0.20, max_cost=0.25,
            ready_timeout=600.0, label="offer_id=42 host_id=54454",
        )
    msg = str(excinfo.value)
    assert "failure_state=" in msg
    assert "OCI runtime" in msg
    assert "could not apply required module" in msg


# ---- _select_offers — excluded_hosts ----------------------------------------


def test_select_offers_filters_excluded_hosts(monkeypatch):
    monkeypatch.setattr(
        whisper_client,
        "_vast",
        lambda *_a, **_k: {
            "offers": [
                _offer(1, host_id=100, price=0.30),
                _offer(2, host_id=200, price=0.31),
                _offer(3, host_id=100, price=0.32),  # same broken host
                _offer(4, host_id=300, price=0.33),
            ],
        },
    )
    candidates = _select_offers(
        "vast-test-key",
        max_price=settings.vast_max_price_per_hour,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=settings.vast_min_cuda,
        excluded_hosts={100},
    )
    assert sorted(o["id"] for o in candidates) == [2, 4]


def test_select_offers_excluded_hosts_default_none_keeps_existing_behavior(monkeypatch):
    monkeypatch.setattr(
        whisper_client,
        "_vast",
        lambda *_a, **_k: {"offers": [_offer(1, host_id=100), _offer(2, host_id=200)]},
    )
    candidates = _select_offers(
        "vast-test-key",
        max_price=settings.vast_max_price_per_hour,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=settings.vast_min_cuda,
    )
    assert sorted(o["id"] for o in candidates) == [1, 2]


# ---- main loop — host blacklisted after a failed offer ----------------------


def _stub_run_context(monkeypatch, tmp_path):
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 30)
    monkeypatch.setattr(whisper_client, "_ensure_local_ssh_key", lambda: (key_path, "ssh-ed25519 t"))
    monkeypatch.setattr(whisper_client, "_ensure_vast_ssh_key", lambda *_a, **_k: None)


def test_transcribe_loop_blacklists_failed_host_within_job(monkeypatch, tmp_path):
    """When offer A on host=H fails to start, a sibling offer B on the same
    host=H must be skipped without spending an attempt."""
    _stub_run_context(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "vast_offer_attempts", 5)

    fixture = [
        _offer(101, host_id=54454, price=0.30),  # broken host — fails ssh wait
        _offer(102, host_id=54454, price=0.31),  # SAME host — must be skipped
        _offer(103, host_id=99999, price=0.32),  # different host — also fails (test ends here)
    ]
    monkeypatch.setattr(whisper_client, "_select_offers", lambda *_a, **_k: list(fixture))

    create_calls: list[int] = []
    monkeypatch.setattr(
        whisper_client,
        "_create_instance",
        lambda _api_key, offer, _public_key: (create_calls.append(int(offer["id"])), 9000 + int(offer["id"]))[1],
    )
    monkeypatch.setattr(whisper_client, "_destroy_instance", lambda *_a, **_k: None)
    monkeypatch.setattr(
        whisper_client,
        "_wait_for_ssh",
        lambda *_a, **_k: (_ for _ in ()).throw(VastInstanceFailedError("Vast container failed: failure_state=exited")),
    )

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="no Vast instance became ready"):
        whisper_client.transcribe(
            wav,
            title="blacklist video",
            source_url="https://youtu.be/blacklist",
        )

    # Only 101 (failed) and 103 (different host) should have been tried.
    # 102 was skipped because its host was blacklisted by 101's failure.
    assert create_calls == [101, 103]
