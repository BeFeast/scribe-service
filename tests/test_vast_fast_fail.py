"""Tests for scr-271 fast-fail + per-job host blacklist + ready-timeout vs cost-budget."""
from __future__ import annotations

import time

import pytest

from scribe.config import settings
from scribe.pipeline import whisper_client
from scribe.pipeline.whisper_client import (
    VastBudgetExceededError,
    VastInstanceFailedError,
    VastReadyTimeoutError,
    WhisperError,
    _ensure_budget,
    _select_offers,
    _vast_failure_state,
    _wait_for_ssh,
    _wait_remote_ready,
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
    # ready window opened 700s ago and the 600s cap is long past — ready_timeout
    # fired, not the cost budget (which at $0.20/h would need ~4500s).
    ready_started = time.monotonic() - 700.0
    started = ready_started  # cost-budget start coincides for this single-attempt case
    deadline = time.monotonic() - 1.0  # already expired
    with pytest.raises(VastReadyTimeoutError, match="ready_timeout exceeded"):
        _ensure_budget(
            started, deadline, price=0.20, max_cost=0.25,
            ready_timeout=600.0, ready_started=ready_started, label="offer_id=42 host_id=99",
        )


def test_ensure_budget_cost_cap_message_when_no_ready_timeout():
    """Without ready_timeout context, the existing cost-cap message wins."""
    started = time.monotonic() - 700.0
    deadline = time.monotonic() - 1.0
    with pytest.raises(WhisperError) as excinfo:
        _ensure_budget(started, deadline, price=0.20, max_cost=0.25)
    assert "budget guard tripped" in str(excinfo.value)
    assert not isinstance(excinfo.value, VastReadyTimeoutError)


def test_ensure_budget_label_appears_in_message():
    ready_started = time.monotonic() - 700.0
    started = ready_started
    deadline = time.monotonic() - 1.0
    with pytest.raises(VastReadyTimeoutError, match=r"\(offer_id=7 host_id=42\)"):
        _ensure_budget(
            started, deadline, price=0.20, max_cost=0.25,
            ready_timeout=600.0, ready_started=ready_started, label="offer_id=7 host_id=42",
        )


def test_ensure_budget_classifies_cost_cap_when_cumulative_elapsed_exceeds_ready_timeout():
    """Regression for the multi-offer misroute: prior attempts burned 650s of
    cumulative job time, but THIS offer's ready window only opened 50s ago.
    Even though cumulative elapsed (650s) >= ready_timeout (600s), the
    per-attempt ready_elapsed (50s) is well under, so this must be reported
    as a cost-cap, not a ready_timeout — otherwise operators chase the wrong
    root cause (the exact bug from issue #271)."""
    now = time.monotonic()
    started = now - 650.0          # cumulative job elapsed = 650s (>= 600s ready_timeout)
    ready_started = now - 50.0     # this offer's ready window = 50s (< 600s)
    deadline = now - 1.0           # deadline already expired (cost-cap path)
    with pytest.raises(VastBudgetExceededError) as excinfo:
        _ensure_budget(
            started, deadline, price=0.20, max_cost=0.25,
            ready_timeout=600.0, ready_started=ready_started, label="offer_id=8 host_id=7",
        )
    assert "budget guard tripped" in str(excinfo.value)
    assert not isinstance(excinfo.value, VastReadyTimeoutError)


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


def test_wait_remote_ready_fast_fails_on_failed_actual_status(monkeypatch, tmp_path):
    """A container that comes up enough for SSH but then transitions to a
    terminal-failure state mid-startup (e.g. nvidia-smi panics, OOM-killed)
    must raise VastInstanceFailedError from _wait_remote_ready on the next
    poll, not keep retrying the readiness probe for ready_timeout."""
    key = tmp_path / "id"
    key.write_text("k", encoding="utf-8")
    started = time.monotonic()
    deadline = started + 600.0

    monkeypatch.setattr(
        whisper_client,
        "_get_instance",
        lambda _api_key, _id: {
            "actual_status": "running",
            "cur_state": "crashed",
            "status_msg": "nvidia-smi: command not found; CUDA driver mismatch",
        },
    )
    # The readiness ssh probe must never run -- the API status check fires first.
    monkeypatch.setattr(
        whisper_client,
        "_run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("readiness probe must not run")),
    )

    with pytest.raises(VastInstanceFailedError) as excinfo:
        _wait_remote_ready(
            "vast-test-key", 9999, "10.0.0.1", 22, key, started, deadline, price=0.20, max_cost=0.25,
            ready_timeout=600.0, ready_started=started, label="offer_id=42 host_id=54454",
        )
    msg = str(excinfo.value)
    assert "failed mid-startup" in msg
    assert "failure_state=crashed" in msg
    assert "nvidia-smi" in msg


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


def test_transcribe_loop_does_not_blacklist_host_on_cost_cap(monkeypatch, tmp_path):
    """A cost-cap (VastBudgetExceededError) is a job-budget condition, not a
    bad host. A sibling offer on the same host must still be tried — don't
    skip a healthy host just because the per-attempt cost deadline fired."""
    _stub_run_context(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "vast_offer_attempts", 5)

    fixture = [
        _offer(101, host_id=54454, price=0.30),  # cost-cap fires here
        _offer(102, host_id=54454, price=0.31),  # SAME host — must NOT be skipped
        _offer(103, host_id=99999, price=0.32),  # different host — also fails (test ends)
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
        lambda *_a, **_k: (_ for _ in ()).throw(VastBudgetExceededError("Vast budget guard tripped after 380s")),
    )

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")

    with pytest.raises(WhisperError, match="no Vast instance became ready"):
        whisper_client.transcribe(
            wav,
            title="cost-cap video",
            source_url="https://youtu.be/costcap",
        )

    # cost-cap is job-budget, not host-side: both sibling offers on host 54454
    # are attempted (101 and 102), then 103 on a different host.
    assert create_calls == [101, 102, 103]
