"""/readyz is the deep readiness probe: it reuses the obs/ops.py probes and
returns 503 with a per-subsystem breakdown when any required subsystem is
degraded. The fast /healthz liveness probe is preserved (unconditional ok).
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version

from fastapi.testclient import TestClient

from scribe.main import app
from scribe.obs import ops


def _fresh_backup_payload() -> dict:
    return {
        "path": "/tmp/heartbeat",
        "last_success_ts": 1,
        "last_success_iso": "2026-01-01T00:00:00+00:00",
        "age_seconds": 1,
        "stale_after_seconds": 90_000,
        "stale": False,
    }


def _stale_backup_payload() -> dict:
    return {
        "path": "/tmp/heartbeat",
        "last_success_ts": None,
        "last_success_iso": None,
        "age_seconds": None,
        "stale_after_seconds": 90_000,
        "stale": True,
        "error": "no backup recorded yet",
    }


def test_readyz_200_when_all_subsystems_ok(monkeypatch) -> None:
    monkeypatch.setattr(ops, "_probe_postgres", lambda: ("ready · 1 conn", "ok"))
    monkeypatch.setattr(ops, "_probe_vast", lambda: ("last launch fresh", "ok"))
    monkeypatch.setattr(ops, "_probe_codex", lambda: ("last success fresh", "ok"))
    monkeypatch.setattr(ops, "_backup_heartbeat", _fresh_backup_payload)

    with TestClient(app) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == pkg_version("scribe")
    subsystems = body["subsystems"]
    assert set(subsystems) == {"postgres", "vast", "codex", "backup"}
    for item in subsystems.values():
        assert item["status"] == "ok"
        assert {"label", "value", "status"} <= set(item)


def test_readyz_503_when_subsystem_degraded(monkeypatch) -> None:
    """A degraded (warn) required subsystem must trip 503 with the breakdown."""
    monkeypatch.setattr(ops, "_probe_postgres", lambda: ("ready · 1 conn", "ok"))
    monkeypatch.setattr(ops, "_probe_vast", lambda: ("no recent launches recorded", "warn"))
    monkeypatch.setattr(ops, "_probe_codex", lambda: ("last success fresh", "ok"))
    monkeypatch.setattr(ops, "_backup_heartbeat", _fresh_backup_payload)

    with TestClient(app) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["subsystems"]["vast"]["status"] == "warn"
    assert "no recent" in body["subsystems"]["vast"]["value"]
    # The non-degraded subsystems are still reported as ok.
    assert body["subsystems"]["postgres"]["status"] == "ok"
    assert body["subsystems"]["codex"]["status"] == "ok"
    assert body["subsystems"]["backup"]["status"] == "ok"


def test_readyz_503_when_postgres_hard_down(monkeypatch) -> None:
    """A hard probe failure (err) must also trip 503."""

    def _boom() -> tuple[str, ops.Status]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ops, "_probe_postgres", _boom)
    monkeypatch.setattr(ops, "_probe_vast", lambda: ("last launch fresh", "ok"))
    monkeypatch.setattr(ops, "_probe_codex", lambda: ("last success fresh", "ok"))
    monkeypatch.setattr(ops, "_backup_heartbeat", _fresh_backup_payload)

    with TestClient(app) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["subsystems"]["postgres"]["status"] == "warn"
    assert "probe failed" in body["subsystems"]["postgres"]["value"]


def test_readyz_503_when_backup_stale(monkeypatch) -> None:
    monkeypatch.setattr(ops, "_probe_postgres", lambda: ("ready · 1 conn", "ok"))
    monkeypatch.setattr(ops, "_probe_vast", lambda: ("last launch fresh", "ok"))
    monkeypatch.setattr(ops, "_probe_codex", lambda: ("last success fresh", "ok"))
    monkeypatch.setattr(ops, "_backup_heartbeat", _stale_backup_payload)

    with TestClient(app) as client:
        resp = client.get("/readyz")

    assert resp.status_code == 503
    body = resp.json()
    assert body["subsystems"]["backup"]["status"] == "err"
    assert body["subsystems"]["backup"]["heartbeat"]["stale"] is True


def test_healthz_stays_unconditional_liveness() -> None:
    """/healthz must not run subsystem probes — it is the fast liveness probe."""
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == pkg_version("scribe")
    # Liveness must not surface a per-subsystem breakdown.
    assert "subsystems" not in body
