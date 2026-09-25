from __future__ import annotations

import subprocess

import pytest

from scribe.config import settings
from scribe.pipeline import whisper_client
from scribe.worker import vast_reaper


def test_destroy_refuses_pinned_instance(monkeypatch):
    monkeypatch.setattr(settings, "pinned_vast_id", 48673124)
    calls: list[tuple] = []

    def fake_vast(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("vast API must not be called for pinned destroy")

    monkeypatch.setattr(whisper_client, "_vast", fake_vast)
    whisper_client._destroy_instance("key", 48673124)
    assert calls == []


def test_reaper_skips_pinned_instance(monkeypatch):
    monkeypatch.setattr(settings, "pinned_vast_id", 48673124)
    instance = {
        "id": 48673124,
        "label": "meeting-transcription-lane1",
        "actual_status": "running",
    }
    assert vast_reaper._is_scribe_instance(instance) is False
    scribe = {
        "id": 1,
        "label": "job-scribe-whisper-20260825T000000Z",
        "actual_status": "running",
    }
    assert vast_reaper._is_scribe_instance(scribe) is True


def test_pinned_busy_falls_back_to_market(monkeypatch, tmp_path):
    import subprocess

    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 30)
    monkeypatch.setattr(settings, "pinned_vast_id", 48673124)
    monkeypatch.setattr(settings, "pinned_ssh_host", "ssh3.vast.ai")
    monkeypatch.setattr(settings, "pinned_ssh_key", str(key_path))
    monkeypatch.setattr(whisper_client, "_ensure_local_ssh_key", lambda: (key_path, "ssh-ed25519 test"))
    monkeypatch.setattr(whisper_client, "_ensure_vast_ssh_key", lambda *_a, **_k: None)

    monkeypatch.setattr(
        whisper_client,
        "_transcribe_pinned",
        lambda *_a, **_k: (_ for _ in ()).throw(whisper_client.PinnedGpuBusy("busy")),
    )
    monkeypatch.setattr(
        whisper_client, "_select_offers", lambda *_a, **_k: [{"id": 1, "gpu_name": "RTX 3090", "dph_total": 0.3, "cuda_max_good": 12.8, "reliability": 0.99, "inet_down": 1000, "gpu_frac": 1.0}]
    )
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_a, **_k: 4242)
    monkeypatch.setattr(whisper_client, "_wait_for_ssh", lambda *_a, **_k: ("gpu.example", 22))
    monkeypatch.setattr(whisper_client, "_wait_remote_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        whisper_client, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", "")
    )
    monkeypatch.setattr(whisper_client, "_scp_to", lambda *_a, **_k: None)

    def fake_scp_from(_host, _port, _key, src, target, **_k):
        if str(src).endswith("result.json"):
            target.write_text(
                '{"detected_language": "en", "duration_seconds": 1, "backend": "market"}',
                encoding="utf-8",
            )
        else:
            target.write_text("# market", encoding="utf-8")

    monkeypatch.setattr(whisper_client, "_scp_from", fake_scp_from)
    destroyed: list[int] = []
    monkeypatch.setattr(
        whisper_client, "_destroy_instance", lambda _k, instance_id: destroyed.append(instance_id)
    )

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")
    result = whisper_client.transcribe(
        wav, title="busy", source_url="https://youtu.be/busy"
    )
    assert result.transcript_md == "# market"
    assert result.vast_instance_id == 4242
    assert destroyed == [4242]


def test_pinned_success_does_not_destroy(monkeypatch, tmp_path):
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "pinned_vast_id", 48673124)
    monkeypatch.setattr(settings, "pinned_ssh_host", "ssh3.vast.ai")
    monkeypatch.setattr(settings, "pinned_ssh_key", str(key_path))
    monkeypatch.setattr(settings, "pinned_hourly_usd", 0.058)

    def fake_pinned(*_a, **_k):
        return whisper_client.TranscribeResult(
            transcript_md="# pinned",
            detected_language="en",
            duration_seconds=12.0,
            backend="pinned-gpu",
            vast_instance_id=48673124,
            vast_cost=0.001,
        )

    monkeypatch.setattr(whisper_client, "_transcribe_pinned", fake_pinned)
    created: list[int] = []
    destroyed: list[int] = []
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_a, **_k: created.append(1) or 1)
    monkeypatch.setattr(whisper_client, "_destroy_instance", lambda *_a, **_k: destroyed.append(1))

    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")
    result = whisper_client.transcribe(
        wav, title="pin", source_url="https://youtu.be/pin"
    )
    assert result.transcript_md == "# pinned"
    assert result.vast_instance_id == 48673124
    assert created == []
    assert destroyed == []


def _configure_pinned(monkeypatch, tmp_path):
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("key", encoding="utf-8")
    monkeypatch.setattr(settings, "vast_api_key", "vast-test-key")
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 30)
    monkeypatch.setattr(settings, "pinned_vast_id", 48673124)
    monkeypatch.setattr(settings, "pinned_ssh_host", "ssh3.vast.ai")
    monkeypatch.setattr(settings, "pinned_ssh_key", str(key_path))
    wav = tmp_path / "input-16k.wav"
    wav.write_text("wav", encoding="utf-8")
    return key_path, wav


def _pinned_kwargs() -> dict:
    return dict(
        title="down",
        source_url="https://youtu.be/down",
        model_size="large-v3-turbo",
        compute_type="float16",
        language="auto",
        beam_size=5,
    )


def _ssh_refused(cmd: list[str]) -> whisper_client.WhisperError:
    # Mirrors _run()'s message for `ssh: connect ... Connection refused` (exit 255, no stderr under -q).
    return whisper_client.WhisperError(f"command failed (255): {' '.join(cmd)}\nstdout:\n\nstderr:\n")


def _forbid_remote_job(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("remote transcription must not start when transport failed")

    monkeypatch.setattr(subprocess, "run", fail)


def test_pinned_mkdir_failure_is_unavailable(monkeypatch, tmp_path):
    _, wav = _configure_pinned(monkeypatch, tmp_path)
    _forbid_remote_job(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        raise _ssh_refused(cmd)

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    with pytest.raises(whisper_client.PinnedGpuUnavailable) as excinfo:
        whisper_client._transcribe_pinned(wav, **_pinned_kwargs())
    assert str(excinfo.value) == "pinned GPU mkdir command failed (255): no stderr"
    assert isinstance(excinfo.value.__cause__, whisper_client.WhisperError)
    assert len(calls) == 1 and calls[0][0] == "ssh"


def test_pinned_upload_failure_is_unavailable(monkeypatch, tmp_path):
    _, wav = _configure_pinned(monkeypatch, tmp_path)
    _forbid_remote_job(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        if cmd[0] == "scp":
            raise whisper_client.WhisperError(
                f"command failed (255): {' '.join(cmd)}\nstdout:\n\nstderr:\nssh: connect to host ssh3.vast.ai port 13632: Connection refused\nlost connection\n"
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    with pytest.raises(whisper_client.PinnedGpuUnavailable) as excinfo:
        whisper_client._transcribe_pinned(wav, **_pinned_kwargs())
    assert str(excinfo.value) == (
        "pinned GPU audio upload command failed (255): "
        "ssh: connect to host ssh3.vast.ai port 13632: Connection refused\nlost connection"
    )
    assert [cmd[0] for cmd in calls] == ["ssh", "scp"]


def test_pinned_upload_timeout_is_unavailable(monkeypatch, tmp_path):
    _, wav = _configure_pinned(monkeypatch, tmp_path)
    _forbid_remote_job(monkeypatch)

    def fake_run(cmd, *, timeout=None, **_k):
        if cmd[0] == "scp":
            raise whisper_client.WhisperError(f"command timed out after {timeout}s: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    with pytest.raises(whisper_client.PinnedGpuUnavailable) as excinfo:
        whisper_client._transcribe_pinned(wav, **_pinned_kwargs())
    assert str(excinfo.value) == "pinned GPU audio upload command timed out after 600s: no stderr"


def test_pinned_result_download_failure_is_unavailable(monkeypatch, tmp_path):
    _, wav = _configure_pinned(monkeypatch, tmp_path)

    def fake_remote_job(cmd, **_k):
        assert cmd[0] == "ssh"
        return subprocess.CompletedProcess(cmd, 0, "TITLE:down\n", "")

    monkeypatch.setattr(subprocess, "run", fake_remote_job)

    def fake_run(cmd, **_k):
        if cmd[0] == "scp" and cmd[-2].endswith("result.json"):
            raise _ssh_refused(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    with pytest.raises(whisper_client.PinnedGpuUnavailable) as excinfo:
        whisper_client._transcribe_pinned(wav, **_pinned_kwargs())
    assert str(excinfo.value) == "pinned GPU result.json download command failed (255): no stderr"


def test_pinned_unreachable_falls_back_to_market(monkeypatch, tmp_path, capsys):
    key_path, wav = _configure_pinned(monkeypatch, tmp_path)
    monkeypatch.setattr(whisper_client, "_ensure_local_ssh_key", lambda: (key_path, "ssh-ed25519 test"))
    monkeypatch.setattr(whisper_client, "_ensure_vast_ssh_key", lambda *_a, **_k: None)
    _forbid_remote_job(monkeypatch)

    pinned_calls: list[list[str]] = []

    def fake_run(cmd, **_k):
        # The pinned host refuses every connection; the market instance answers normally.
        if "ssh3.vast.ai" in " ".join(cmd):
            pinned_calls.append(cmd)
            raise _ssh_refused(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(whisper_client, "_run", fake_run)
    monkeypatch.setattr(
        whisper_client, "_select_offers", lambda *_a, **_k: [{"id": 1, "gpu_name": "RTX 3090", "dph_total": 0.3, "cuda_max_good": 12.8, "reliability": 0.99, "inet_down": 1000, "gpu_frac": 1.0}]
    )
    monkeypatch.setattr(whisper_client, "_create_instance", lambda *_a, **_k: 4242)
    monkeypatch.setattr(whisper_client, "_wait_for_ssh", lambda *_a, **_k: ("gpu.example", 22))
    monkeypatch.setattr(whisper_client, "_wait_remote_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(whisper_client, "_scp_to", lambda *_a, **_k: None)

    def fake_scp_from(_host, _port, _key, src, target, **_k):
        if str(src).endswith("result.json"):
            target.write_text(
                '{"detected_language": "en", "duration_seconds": 1, "backend": "market"}',
                encoding="utf-8",
            )
        else:
            target.write_text("# market", encoding="utf-8")

    monkeypatch.setattr(whisper_client, "_scp_from", fake_scp_from)
    destroyed: list[int] = []
    monkeypatch.setattr(
        whisper_client, "_destroy_instance", lambda _k, instance_id: destroyed.append(instance_id)
    )

    result = whisper_client.transcribe(wav, title="down", source_url="https://youtu.be/down")
    assert result.transcript_md == "# market"
    assert result.vast_instance_id == 4242
    assert destroyed == [4242]
    assert len(pinned_calls) == 1 and pinned_calls[0][0] == "ssh"
    assert "Notice: pinned GPU unavailable (pinned GPU mkdir command failed (255): no stderr); falling back to market Vast" in capsys.readouterr().err
