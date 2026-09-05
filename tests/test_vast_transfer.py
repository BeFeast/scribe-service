"""Recovery stays on the paid instance and within its transfer/job deadline."""
from __future__ import annotations

import subprocess
from collections import Counter
from types import SimpleNamespace

import pytest

from scribe.config import settings
from scribe.pipeline import whisper_client as wc


@pytest.fixture
def transfer_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(wc, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(wc, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


@pytest.mark.parametrize("alternate", [False, True])
def test_timeout_recovers_on_same_instance(monkeypatch, transfer_clock, alternate):
    calls = []
    endpoints = [("direct", 22)] + ([("proxy", 23)] if alternate else [])

    def transfer(host, port, timeout):
        calls.append((host, port, timeout))
        if len(calls) == 1:
            transfer_clock[0] += timeout
            raise wc.WhisperError("command timed out")

    selected = wc._transfer_with_retry(
        wc._TranscribeRunContext(), transfer, endpoints,
        deadline=1000, max_seconds=600, label="audio",
    )
    assert selected == (("proxy", 23) if alternate else ("direct", 22))
    assert len(calls) == 2
    assert calls[0][2] == 600
    assert transfer_clock[0] < 1000
    assert endpoints[0] == selected


def test_retries_share_deadline(transfer_clock):
    calls = []

    def transfer(_host, _port, timeout):
        calls.append(timeout)
        transfer_clock[0] += timeout
        raise wc.WhisperError("command timed out")

    with pytest.raises(wc.VastBudgetExceededError):
        wc._transfer_with_retry(
            wc._TranscribeRunContext(), transfer, [("direct", 22), ("proxy", 23)],
            deadline=30, max_seconds=600, label="audio",
        )
    assert len(calls) == 1
    assert transfer_clock[0] == pytest.approx(30)


def test_expired_budget_never_starts_transfer(transfer_clock):
    with pytest.raises(wc.VastBudgetExceededError):
        wc._transfer_with_retry(
            wc._TranscribeRunContext(), lambda *_a: pytest.fail("transfer after deadline"),
            [("direct", 22)], deadline=0, max_seconds=600, label="audio",
        )


def test_cancelled_transfer_does_not_retry(transfer_clock):
    context = wc._TranscribeRunContext()
    calls = []

    def transfer(*_args):
        calls.append(1)
        context.cancel()
        raise wc.WhisperError("connection reset")

    with pytest.raises(wc.TranscribeTimeoutError):
        wc._transfer_with_retry(
            context, transfer, [("direct", 22)], deadline=600, max_seconds=600, label="audio",
        )
    assert calls == [1]


def test_missing_file_is_not_retried(transfer_clock):
    calls = []

    def transfer(*_args):
        calls.append(1)
        raise wc.WhisperError("No such file or directory")

    with pytest.raises(wc.WhisperError, match="No such file"):
        wc._transfer_with_retry(
            wc._TranscribeRunContext(), transfer, [("direct", 22)],
            deadline=600, max_seconds=600, label="audio",
        )
    assert calls == [1]


@pytest.mark.parametrize("failed_file", ["input-16k.wav", "result.json", "transcript.md"])
def test_transcribe_recovers_upload_and_paid_result(monkeypatch, tmp_path, failed_file):
    monkeypatch.setattr(settings, "vast_api_key", "test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 60)
    monkeypatch.setattr(wc, "_ensure_local_ssh_key", lambda: (tmp_path / "key", "test-public"))
    monkeypatch.setattr(wc, "_ensure_vast_ssh_key", lambda *_a: None)
    monkeypatch.setattr(wc, "_select_offers", lambda *_a, **_k: [{"id": 1, "dph_total": 0.3}])
    created, destroyed = [], []

    def create(*_args):
        created.append(4242)
        return 4242

    def ready(*_args, endpoints, **_kwargs):
        endpoints[:] = [("direct", 22), ("proxy", 23)]
        return "direct", 22

    monkeypatch.setattr(wc, "_create_instance", create)
    monkeypatch.setattr(wc, "_wait_for_ssh", ready)
    monkeypatch.setattr(wc, "_wait_remote_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(wc, "_destroy_instance", lambda _key, instance: destroyed.append(instance))
    monkeypatch.setattr(wc, "_sleep", lambda _seconds: None)
    transfers = []
    counts = Counter()
    executions = []

    def run(cmd, *, timeout=None, **_kwargs):
        assert 0 < timeout <= 60
        if cmd[0] == "scp":
            source, target = cmd[-2:]
            remote = target if target.startswith("root@") else source
            file_name = remote.rsplit("/", 1)[-1]
            counts[file_name] += 1
            transfers.append(remote)
            assert "BatchMode=yes" in cmd
            assert "ConnectTimeout=30" in cmd
            if file_name == failed_file and counts[file_name] == 1:
                raise wc.WhisperError("command timed out")
            if source.startswith("root@"):
                from pathlib import Path
                Path(target).write_text(
                    '{"detected_language":"en","backend":"fake"}' if file_name == "result.json"
                    else "# recovered transcript", encoding="utf-8",
                )
        elif "remote_transcribe.py" in cmd[-1]:
            executions.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wc, "_run", run)
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"audio")
    result = wc._transcribe_impl(wc._TranscribeRunContext(), wav, title="test", source_url="test")
    assert result.transcript_md == "# recovered transcript"
    assert result.vast_instance_id == 4242
    assert created == destroyed == [4242]
    assert counts[failed_file] == 2
    assert any(remote.startswith("root@proxy:") and remote.endswith(failed_file) for remote in transfers)
    assert len(executions) == 1
    if failed_file == "input-16k.wav":
        assert "root@proxy" in executions[0]


def test_healthy_long_upload_keeps_existing_timeout(transfer_clock):
    def transfer(_host, _port, timeout):
        assert timeout == 600
        transfer_clock[0] += 500

    assert wc._transfer_with_retry(
        wc._TranscribeRunContext(), transfer, [("direct", 22)],
        deadline=1800, max_seconds=600, label="audio",
    ) == ("direct", 22)


def test_persistent_failure_stops_after_three_attempts(transfer_clock):
    calls = []

    def transfer(*_args):
        calls.append(1)
        raise wc.WhisperError("connection reset")

    with pytest.raises(wc.WhisperError, match="connection reset"):
        wc._transfer_with_retry(
            wc._TranscribeRunContext(), transfer, [("direct", 22)],
            deadline=1800, max_seconds=600, label="audio",
        )
    assert len(calls) == 3
