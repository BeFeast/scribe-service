"""Tests for scribe.worker.download_canary."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scribe.obs import metrics
from scribe.pipeline.downloader import DownloadError, DownloadResult
from scribe.worker import download_canary


def _gauge(g) -> float:
    return metrics.gauge_value(g)


def _reset_metrics() -> None:
    metrics.download_canary_status.set(-1)
    metrics.download_canary_last_success_timestamp.set(-1)


def test_run_download_canary_marks_green_on_success(monkeypatch, tmp_path: Path):
    _reset_metrics()
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"xx")

    def fake_download(url, dest_dir, **_kw):
        # The canary uses a fresh TemporaryDirectory; mimic that contract by
        # writing into whatever dest_dir the caller supplied.
        target = Path(dest_dir) / "audio.m4a"
        target.write_bytes(b"xx")
        return DownloadResult(
            audio_path=target,
            title="Me at the zoo",
            video_id="jNQXAC9IVRw",
            duration_seconds=19,
        )

    monkeypatch.setattr(download_canary, "download_audio", fake_download)
    with patch.object(download_canary, "send_admin_alert") as alert:
        assert download_canary.run_download_canary("https://example/") is True
        alert.assert_not_called()
    assert _gauge(metrics.download_canary_status) == 1
    assert _gauge(metrics.download_canary_last_success_timestamp) > 0


def test_run_download_canary_marks_red_and_alerts_on_download_error(monkeypatch):
    _reset_metrics()

    def boom(url, dest_dir, **_kw):
        raise DownloadError("LOGIN_REQUIRED: Sign in to confirm you're not a bot")

    monkeypatch.setattr(download_canary, "download_audio", boom)
    with patch.object(download_canary, "send_admin_alert", return_value=True) as alert:
        assert download_canary.run_download_canary("https://example/") is False
        alert.assert_called_once()
        sent = alert.call_args.args[0]
        assert "RED" in sent
        assert "LOGIN_REQUIRED" in sent
        # Runbook link is included so the on-call doesn't need to remember it.
        assert "runbook" in sent.lower()
    assert _gauge(metrics.download_canary_status) == 0


def test_run_download_canary_treats_empty_file_as_failure(monkeypatch, tmp_path: Path):
    _reset_metrics()

    def fake_download(url, dest_dir, **_kw):
        target = Path(dest_dir) / "audio.m4a"
        target.write_bytes(b"")  # zero-byte file
        return DownloadResult(
            audio_path=target,
            title="t",
            video_id="v",
            duration_seconds=1,
        )

    monkeypatch.setattr(download_canary, "download_audio", fake_download)
    with patch.object(download_canary, "send_admin_alert", return_value=True) as alert:
        assert download_canary.run_download_canary("https://example/") is False
        alert.assert_called_once()
    assert _gauge(metrics.download_canary_status) == 0


def test_run_download_canary_swallows_unexpected_errors(monkeypatch):
    _reset_metrics()

    def boom(url, dest_dir, **_kw):
        raise RuntimeError("subprocess vanished")

    monkeypatch.setattr(download_canary, "download_audio", boom)
    with patch.object(download_canary, "send_admin_alert", return_value=True) as alert:
        # Must not raise — a broken canary must never crash the worker process.
        assert download_canary.run_download_canary("https://example/") is False
        alert.assert_called_once()
    assert _gauge(metrics.download_canary_status) == 0


def test_run_download_canary_skips_empty_url(monkeypatch):
    _reset_metrics()
    monkeypatch.setattr(download_canary.settings, "download_canary_url", "")
    with patch.object(download_canary, "send_admin_alert") as alert:
        assert download_canary.run_download_canary() is False
        alert.assert_not_called()
    # Status stays at -1 (never-run) when there is no url to probe.
    assert _gauge(metrics.download_canary_status) == -1
