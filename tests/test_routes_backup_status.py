"""Tests for GET /admin/backup-status — file-only, no DB."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from scribe.config import settings
from scribe.main import app


def _client(monkeypatch, *, path: Path, stale_after: int = 90_000) -> TestClient:
    monkeypatch.setattr(settings, "backup_status_path", str(path))
    monkeypatch.setattr(settings, "backup_stale_after_seconds", stale_after)
    return TestClient(app)


def test_backup_status_missing_file_returns_stale(tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    with _client(monkeypatch, path=path) as client:
        resp = client.get("/admin/backup-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_success_ts"] is None
    assert body["age_seconds"] is None
    assert body["stale"] is True
    assert "error" in body


def test_backup_status_fresh_heartbeat_not_stale(tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    now = int(time.time())
    path.write_text(f"{now}\n")
    with _client(monkeypatch, path=path) as client:
        resp = client.get("/admin/backup-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_success_ts"] == now
    assert body["last_success_iso"].endswith("+00:00")
    assert body["age_seconds"] is not None and body["age_seconds"] < 5
    assert body["stale"] is False


def test_backup_status_old_heartbeat_is_stale(tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    # 2 days ago — well past the 25h default threshold.
    old = int(time.time()) - 2 * 86400
    path.write_text(str(old))
    with _client(monkeypatch, path=path) as client:
        resp = client.get("/admin/backup-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_success_ts"] == old
    assert body["stale"] is True


def test_backup_status_unreadable_value_returns_stale(tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    path.write_text("not-an-int")
    with _client(monkeypatch, path=path) as client:
        resp = client.get("/admin/backup-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_success_ts"] is None
    assert body["stale"] is True
    assert "unreadable" in body["error"]


def test_backup_status_threshold_zero_disables_staleness(tmp_path, monkeypatch):
    path = tmp_path / "_last_success_ts"
    # Ancient heartbeat — would normally be stale, but threshold=0 disables it.
    path.write_text(str(int(time.time()) - 10 * 86400))
    with _client(monkeypatch, path=path, stale_after=0) as client:
        resp = client.get("/admin/backup-status")
    assert resp.status_code == 200
    assert resp.json()["stale"] is False
