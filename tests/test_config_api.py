"""Tests for DB-backed runtime config overlay endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete

from scribe.api import routes as routes_module
from scribe.config import RUNTIME_CONFIG, settings
from scribe.db.models import AppConfig
from scribe.main import app


def _client(db_session) -> TestClient:
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    return TestClient(app)


def _clear_config(session) -> None:
    session.execute(delete(AppConfig))
    session.commit()


def _restore_settings(snapshot: dict[str, object], sources: set[str]) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)
    settings._runtime_sources = set(sources)


def test_get_config_uses_env_fallback(db_session):
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings._runtime_sources = set()
        settings.daily_spend_cap_usd = 12.5

        client = _client(db_session)
        resp = client.get("/api/config")

        assert resp.status_code == 200
        item = resp.json()["config"]["daily_spend_cap_usd"]
        assert item == {"value": 12.5, "source": "env", "mutable": True}
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_get_config_applies_db_override(db_session):
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        db_session.add(AppConfig(key="public_base_url", value="https://scribe.example.test"))
        db_session.commit()

        client = _client(db_session)
        resp = client.get("/api/config")

        assert resp.status_code == 200
        item = resp.json()["config"]["public_base_url"]
        assert item["value"] == "https://scribe.example.test/"
        assert item["source"] == "db"
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_sparse_update_preserves_other_rows(db_session):
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        db_session.add(AppConfig(key="public_base_url", value="https://old.example.test"))
        db_session.commit()

        client = _client(db_session)
        resp = client.post("/api/config", json={"daily_spend_cap_usd": 3.75})

        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["daily_spend_cap_usd"]["value"] == 3.75
        assert body["config"]["daily_spend_cap_usd"]["source"] == "db"
        assert body["config"]["public_base_url"]["value"] == "https://old.example.test/"
        assert body["config"]["public_base_url"]["source"] == "db"
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_rejects_unknown_key(db_session):
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        client = _client(db_session)

        resp = client.post("/api/config", json={"not_a_setting": True})

        assert resp.status_code == 400
        assert "not_a_setting" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_returns_restart_required_for_worker_concurrency(db_session):
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        client = _client(db_session)

        resp = client.post("/api/config", json={"worker_concurrency": 4})

        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["worker_concurrency"]["value"] == 4
        assert body["restart_required"] == ["worker_concurrency"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_rotate_token_stub_returns_501():
    client = TestClient(app)
    resp = client.post("/api/config/rotate-token")
    assert resp.status_code == 501


def test_openapi_includes_config_endpoints():
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/config" in paths
    assert "/api/config/rotate-token" in paths
