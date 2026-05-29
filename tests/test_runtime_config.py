from __future__ import annotations

import logging

import httpx
import pytest

import scribe.config as config_module
from scribe.config import Settings, build_settings
from scribe.runtime_config import InfisicalConfig, RuntimeConfigError, load_infisical_settings


def _config(**overrides: object) -> InfisicalConfig:
    values = {
        "enabled": True,
        "api_url": "https://infisical.example.test",
        "client_id": "fixture-client-id",
        "client_secret": "fixture-client-secret",
        "organization_slug": "",
        "project": "services",
        "environment": "prod",
        "path": "/scribe-service",
        "timeout_seconds": 1.0,
    }
    values.update(overrides)
    return InfisicalConfig(**values)


def test_infisical_disabled_uses_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRIBE_INFISICAL_ENABLED", "false")
    monkeypatch.setenv("SCRIBE_WORKER_CONCURRENCY", "5")
    monkeypatch.delenv("SCRIBE_DAILY_SPEND_CAP_USD", raising=False)

    settings = build_settings()

    assert settings.worker_concurrency == 5
    assert settings.daily_spend_cap_usd == 0.0
    assert settings.vast_budget_baseline_usd_per_hour == 0.05
    assert settings.vast_budget_alert_multiplier == 5.0
    assert settings.display_currency == "ILS"


def test_successful_infisical_load_overlays_expected_keys() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/auth/universal-auth/login":
            return httpx.Response(200, json={"accessToken": "fixture-access-token"})
        if request.url.path == "/api/v1/projects/slug/services":
            assert request.headers["Authorization"] == "Bearer fixture-access-token"
            return httpx.Response(200, json={"id": "project-id-from-slug"})
        if request.url.path == "/api/v4/secrets":
            params = dict(request.url.params)
            assert params["projectId"] == "project-id-from-slug"
            assert params["environment"] == "prod"
            assert params["secretPath"] == "/scribe-service"
            return httpx.Response(
                200,
                json={
                    "secrets": [
                        {"secretKey": "SCRIBE_DATABASE_URL", "secretValue": "postgresql+psycopg://u:p@db/scribe"},
                        {"secretKey": "WORKER_CONCURRENCY", "secretValue": "7"},
                        {"secretKey": "UNRELATED_KEY", "secretValue": "ignored"},
                    ]
                },
            )
        raise AssertionError(f"unexpected request path {request.url.path}")

    client = httpx.Client(
        base_url="https://infisical.example.test",
        transport=httpx.MockTransport(handler),
    )

    overlay = load_infisical_settings(Settings.model_fields, config=_config(), client=client)

    assert overlay == {
        "database_url": "postgresql+psycopg://u:p@db/scribe",
        "worker_concurrency": "7",
    }
    assert [request.url.path for request in requests] == [
        "/api/v1/auth/universal-auth/login",
        "/api/v1/projects/slug/services",
        "/api/v4/secrets",
    ]


def test_env_fallback_still_works_when_infisical_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    client = httpx.Client(
        base_url="https://infisical.example.test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setenv("SCRIBE_PUBLIC_BASE_URL", "https://local.example.test")
    caplog.set_level(logging.WARNING, logger="scribe.runtime_config")

    overlay = load_infisical_settings(
        Settings.model_fields,
        config=_config(),
        client=client,
    )
    settings = Settings(**overlay)

    assert overlay == {}
    assert settings.public_base_url == "https://local.example.test"
    assert "infisical runtime config unavailable" in caplog.text


def test_infisical_secret_values_are_redacted_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed with fixture-client-secret and fixture-client-id")

    client = httpx.Client(
        base_url="https://infisical.example.test",
        transport=httpx.MockTransport(handler),
    )
    caplog.set_level(logging.WARNING, logger="scribe.runtime_config")

    overlay = load_infisical_settings(Settings.model_fields, config=_config(), client=client)

    assert overlay == {}
    assert "fixture-client-secret" not in caplog.text
    assert "fixture-client-id" not in caplog.text
    assert caplog.records[0].error == "failed with [redacted] and [redacted]"


def test_infisical_validation_errors_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "not-a-valid-int-secret"
    monkeypatch.setattr(
        config_module,
        "load_infisical_settings",
        lambda _allowed_keys: {"worker_concurrency": secret_value},
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        build_settings()

    assert secret_value not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)


def test_infisical_freellmapi_api_key_maps_to_settings_field() -> None:
    """Regression for #250: the comment in config.py promises that the
    Infisical loader populates `freellmapi_api_key` from the
    `FREELLMAPI_API_KEY` secret at `services/prod/scribe-service`. Lock that
    mapping in so a future loader refactor cannot silently break the fallback
    chain again."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/universal-auth/login":
            return httpx.Response(200, json={"accessToken": "fixture-access-token"})
        if request.url.path == "/api/v1/projects/slug/services":
            return httpx.Response(200, json={"id": "project-id"})
        if request.url.path == "/api/v4/secrets":
            return httpx.Response(
                200,
                json={
                    "secrets": [
                        {"secretKey": "FREELLMAPI_API_KEY", "secretValue": "free-secret"},
                    ]
                },
            )
        raise AssertionError(f"unexpected request path {request.url.path}")

    client = httpx.Client(
        base_url="https://infisical.example.test",
        transport=httpx.MockTransport(handler),
    )

    overlay = load_infisical_settings(Settings.model_fields, config=_config(), client=client)

    assert overlay == {"freellmapi_api_key": "free-secret"}
    settings = Settings(**overlay)
    assert settings.freellmapi_api_key == "free-secret"
