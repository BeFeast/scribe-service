"""Tests for DB-backed runtime config overlay endpoints."""

from __future__ import annotations

import datetime as dt
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
from sqlalchemy import delete

from scribe.api import routes as routes_module
from scribe.api.auth import clear_jwks_cache
from scribe.config import RUNTIME_CONFIG, RuntimeConfigSpec, Settings, parse_runtime_config_value, settings
from scribe.db.models import AppConfig
from scribe.main import app

TEST_TOKEN = "test-config-token"
MACHINE_TOKEN = "test-machine-token"


def _client(db_session) -> TestClient:
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    return TestClient(app, headers={"Authorization": f"Bearer {TEST_TOKEN}"})


def _clear_config(session) -> None:
    session.execute(delete(AppConfig))
    session.commit()


def _settings_snapshot() -> dict[str, object]:
    snapshot = {key: getattr(settings, key) for key in RUNTIME_CONFIG}
    snapshot["config_api_bearer_token"] = settings.config_api_bearer_token
    snapshot["trusted_cidrs"] = settings.trusted_cidrs
    snapshot["machine_bearer_token"] = settings.machine_bearer_token
    snapshot["auth_allowed_emails"] = settings.auth_allowed_emails
    snapshot["auth_clerk_issuer"] = settings.auth_clerk_issuer
    snapshot["auth_clerk_jwks_url"] = settings.auth_clerk_jwks_url
    snapshot["auth_clerk_jwks_json"] = settings.auth_clerk_jwks_json
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
        owner = resp.json()["config"]["default_owner_email"]
        assert owner == {"value": settings.default_owner_email, "source": "env", "mutable": True}
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

        owner_resp = client.post(
            "/api/config",
            json={
                "default_owner_email": " default@example.test ",
                "default_owner_subject": " user_default ",
            },
        )
        assert owner_resp.status_code == 200
        owner_body = owner_resp.json()["config"]
        assert owner_body["default_owner_email"]["value"] == "default@example.test"
        assert owner_body["default_owner_subject"]["value"] == "user_default"
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


def test_post_config_updates_display_currency(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"display_currency": "ils"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["display_currency"]["value"] == "ILS"
        assert body["config"]["display_currency"]["source"] == "db"
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_rejects_invalid_display_currency(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        settings.config_api_bearer_token = TEST_TOKEN
        client = _client(db_session)

        resp = client.post("/api/config", json={"display_currency": "JPY"})

        assert resp.status_code == 400
        assert "display_currency" in resp.json()["detail"]
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
        RUNTIME_CONFIG["bot_wall_retry"] = RuntimeConfigSpec("bot_wall_retry", "bool", mutable=False)
        client = _client(db_session)

        resp = client.post("/api/config", json={"bot_wall_retry": True})

        assert resp.status_code == 400
        assert "read-only" in resp.json()["detail"]
    finally:
        RUNTIME_CONFIG["bot_wall_retry"] = original
        app.dependency_overrides.pop(routes_module.get_session, None)
        _restore_settings(snapshot, sources)
        _clear_config(db_session)


def test_post_config_requires_operator_auth_from_external_ip(db_session):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    try:
        _clear_config(db_session)
        app.dependency_overrides[routes_module.get_session] = lambda: db_session
        settings.trusted_cidrs = "10.10.0.0/16"
        settings.machine_bearer_token = MACHINE_TOKEN
        client = TestClient(app, client=("203.0.113.10", 50000))

        resp = client.post("/api/config", json={"daily_spend_cap_usd": 1.0})

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


CLERK_ISSUER = "https://clerk.example.test"


@pytest.fixture()
def clerk_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "local-test-key", "alg": "RS256", "use": "sig"})
    return private_key, wrong_private_key, {"keys": [public_jwk]}


@pytest.fixture()
def clerk_auth(clerk_keys):
    snapshot = _settings_snapshot()
    sources = set(settings._runtime_sources)
    _, _, jwks = clerk_keys
    settings.config_api_bearer_token = ""
    settings.trusted_cidrs = "10.10.0.0/16"
    settings.auth_allowed_emails = "allowed@example.test"
    settings.auth_clerk_issuer = CLERK_ISSUER
    settings.auth_clerk_jwks_url = ""
    settings.auth_clerk_jwks_json = json.dumps(jwks)
    clear_jwks_cache()
    try:
        yield
    finally:
        clear_jwks_cache()
        _restore_settings(snapshot, sources)


def _clerk_token(
    private_key,
    *,
    email: str | None = "allowed@example.test",
    issuer: str = CLERK_ISSUER,
    expires_delta: dt.timedelta = dt.timedelta(minutes=5),
) -> str:
    now = dt.datetime.now(dt.UTC)
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": "user_local_fixture",
        "iat": now,
        "exp": now + expires_delta,
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "local-test-key"})


def _post_config_with_clerk(db_session, token: str):
    _clear_config(db_session)
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    try:
        return TestClient(app, client=("203.0.113.10", 50000)).post(
            "/api/config",
            headers={"Authorization": f"Bearer {token}"},
            json={"daily_spend_cap_usd": 1.0},
        )
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)
        _clear_config(db_session)


def test_config_accepts_allowed_clerk_user(db_session, clerk_auth, clerk_keys):
    private_key, _, _ = clerk_keys
    token = _clerk_token(private_key)
    resp = _post_config_with_clerk(db_session, token)
    assert resp.status_code == 200, resp.text


def test_config_rejects_clerk_user_outside_email_allowlist(db_session, clerk_auth, clerk_keys):
    private_key, _, _ = clerk_keys
    token = _clerk_token(private_key, email="other@example.test")
    resp = _post_config_with_clerk(db_session, token)
    assert resp.status_code == 403


def test_config_rejects_clerk_jwt_without_email(db_session, clerk_auth, clerk_keys):
    private_key, _, _ = clerk_keys
    token = _clerk_token(private_key, email=None)
    resp = _post_config_with_clerk(db_session, token)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("token_factory", "expected_status"),
    [
        (lambda private_key, _wrong_key: _clerk_token(private_key, expires_delta=dt.timedelta(minutes=-5)), 401),
        (lambda private_key, _wrong_key: _clerk_token(private_key, issuer="https://wrong.example.test"), 401),
        (lambda _private_key, wrong_key: _clerk_token(wrong_key), 401),
        (lambda _private_key, _wrong_key: "not-a-jwt", 401),
    ],
)
def test_config_rejects_invalid_clerk_jwts(db_session, clerk_auth, clerk_keys, token_factory, expected_status):
    private_key, wrong_key, _ = clerk_keys
    token = token_factory(private_key, wrong_key)
    resp = _post_config_with_clerk(db_session, token)
    assert resp.status_code == expected_status


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


def test_settings_rejects_invalid_trusted_cidr():
    try:
        Settings(trusted_cidrs="10.10.0.0/16,not-a-cidr")
    except ValidationError as exc:
        assert "SCRIBE_TRUSTED_CIDRS contains invalid CIDR" in str(exc)
    else:
        raise AssertionError("accepted malformed trusted CIDR config")


def test_rotate_token_stub_returns_501():
    settings.trusted_cidrs = "127.0.0.0/8"
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
