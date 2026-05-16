"""Tests for DB-backed runtime config overlay endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete

from scribe.api import routes as routes_module
from scribe.config import RUNTIME_CONFIG, RuntimeConfigSpec, parse_runtime_config_value, settings
from scribe.db.models import AppConfig
from scribe.main import app

TEST_TOKEN = "test-config-token"


def _client(db_session) -> TestClient:
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    return TestClient(app, headers={"Authorization": f"Bearer {TEST_TOKEN}"})


def _clear_config(session) -> None:
    session.execute(delete(AppConfig))
    session.commit()


def _settings_snapshot() -> dict[str, object]:
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    snapshot["config_api_bearer_token"] = settings.config_api_bearer_token
    return snapshot


def _restore_settings(snapshot: dict[str, object], sources: set[str]) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)
    settings._runtime_sources = set(sources)


def test_get_config_uses_env_fallback(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
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
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        db_session.add(AppConfig(key="public_base_url", value="https://scribe.example.test"))
        db_session.commit()
        settings.runtime_overlay({"public_base_url": "https://scribe.example.test"})

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
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        db_session.add(AppConfig(key="public_base_url", value="https://old.example.test"))
        db_session.commit()
        settings.runtime_overlay({"public_base_url": "https://old.example.test"})

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
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"not_a_setting": True})

        assert resp.status_code == 400
        assert "not_a_setting" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_rejects_invalid_url(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"public_base_url": "not a url"})

        assert resp.status_code == 400
        assert "public_base_url" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_updates_short_description_language(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"short_description_language": "en"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["short_description_language"]["value"] == "en"
        assert body["config"]["short_description_language"]["source"] == "db"
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_rejects_invalid_short_description_language(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"short_description_language": "de"})

        assert resp.status_code == 400
        assert "short_description_language" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_returns_restart_required_for_worker_concurrency(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
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


def test_post_config_rejects_invalid_existing_overlay_before_commit(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        db_session.add(AppConfig(key="public_base_url", value="not a url"))
        db_session.commit()
        client = _client(db_session)

        resp = client.post("/api/config", json={"daily_spend_cap_usd": 2.5})

        assert resp.status_code == 400
        row = db_session.get(AppConfig, "daily_spend_cap_usd")
        assert row is None
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_rejects_read_only_key(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    original = RUNTIME_CONFIG["bot_wall_retry"]
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        RUNTIME_CONFIG["bot_wall_retry"] = RuntimeConfigSpec(
            "bot_wall_retry", "bool", mutable=False
        )
        client = _client(db_session)

        resp = client.post("/api/config", json={"bot_wall_retry": True})

        assert resp.status_code == 400
        assert "read-only" in resp.json()["detail"]
    finally:
        RUNTIME_CONFIG["bot_wall_retry"] = original
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_config_requires_bearer_token(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        app.dependency_overrides[routes_module.get_session] = lambda: db_session
        client = TestClient(app)

        resp = client.get("/api/config")

        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_config_allows_access_when_bearer_token_unset(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = ""
        app.dependency_overrides[routes_module.get_session] = lambda: db_session
        client = TestClient(app)

        resp = client.get("/api/config")

        assert resp.status_code == 200
        assert "daily_spend_cap_usd" in resp.json()["config"]
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_parse_runtime_config_rejects_non_finite_float():
    for value in (float("nan"), float("inf"), "-inf"):
        try:
            parse_runtime_config_value("daily_spend_cap_usd", value)
        except ValueError as exc:
            assert "finite" in str(exc) or "number" in str(exc)
        else:
            raise AssertionError(f"accepted non-finite value {value!r}")


def test_parse_runtime_config_rejects_fractional_int():
    for value in (3.9, "3.9"):
        try:
            parse_runtime_config_value("worker_concurrency", value)
        except ValueError as exc:
            assert "integer" in str(exc)
        else:
            raise AssertionError(f"accepted fractional integer {value!r}")


def test_rotate_token_stub_returns_501():
    settings.config_api_bearer_token = TEST_TOKEN
    client = TestClient(app, headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    resp = client.post("/api/config/rotate-token")
    assert resp.status_code == 501


def test_openapi_includes_config_endpoints():
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/config" in paths
    assert "/api/config/rotate-token" in paths


def test_prompt_version_is_not_exposed_as_dead_runtime_config():
    assert "prompt_template_active_version" not in RUNTIME_CONFIG
