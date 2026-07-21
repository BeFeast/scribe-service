from __future__ import annotations

import logging

import httpx
import pytest

import scribe.config as config_module
from scribe.config import Settings, build_settings
from scribe.obs.metrics import runtime_config_load_state
from scribe.runtime_config import (
    InfisicalBootPolicy,
    InfisicalConfig,
    OverlayLoad,
    RuntimeConfigError,
    load_infisical_overlay,
    load_infisical_overlay_with_retry,
    load_infisical_settings,
)


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
        "load_infisical_overlay",
        lambda _allowed_keys: OverlayLoad({"worker_concurrency": secret_value}, "infisical"),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        build_settings()

    assert secret_value not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)


def test_build_settings_does_not_clobber_env_with_empty_infisical_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #261: when Infisical returns an empty/blank value for a
    SCRIBE_* secret, `build_settings()` must keep the env value (or the
    pydantic default) rather than overwrite it with an empty string. The old
    behaviour silently nulled out `SCRIBE_MACHINE_BEARER_TOKEN` /
    `SCRIBE_TRUSTED_CIDRS` and locked every LAN client out with `401`."""

    monkeypatch.setenv("SCRIBE_TRUSTED_CIDRS", "10.10.0.0/16")
    monkeypatch.setenv("SCRIBE_MACHINE_BEARER_TOKEN", "env-bearer")
    monkeypatch.setattr(
        config_module,
        "load_infisical_overlay",
        lambda _allowed_keys: OverlayLoad(
            {
                "trusted_cidrs": "",
                "machine_bearer_token": "",
                "public_base_url": "https://infisical.example.test",
            },
            "infisical",
        ),
    )

    settings = build_settings()

    assert settings.trusted_cidrs == "10.10.0.0/16"
    assert settings.machine_bearer_token == "env-bearer"
    # Non-empty Infisical values still take precedence over env defaults.
    assert settings.public_base_url == "https://infisical.example.test"


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


# --- #415 boot hardening ---------------------------------------------------


def _degraded_client() -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    return httpx.Client(
        base_url="https://infisical.example.test",
        transport=httpx.MockTransport(handler),
    )


def test_overlay_state_disabled_when_infisical_not_configured() -> None:
    load = load_infisical_overlay(Settings.model_fields, config=_config(enabled=False))
    assert load.state == "disabled"
    assert load.overlay == {}


def test_overlay_state_degraded_when_unreachable() -> None:
    load = load_infisical_overlay(
        Settings.model_fields, config=_config(), client=_degraded_client()
    )
    assert load.state == "degraded"
    assert load.overlay == {}


def test_boot_policy_from_env_parses_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRIBE_INFISICAL_BOOT_RETRY_ENABLED", "false")
    monkeypatch.setenv("SCRIBE_INFISICAL_BOOT_MAX_SECONDS", "120")
    monkeypatch.setenv("SCRIBE_INFISICAL_BOOT_INITIAL_DELAY_SECONDS", "3")
    monkeypatch.setenv("SCRIBE_INFISICAL_BOOT_MAX_DELAY_SECONDS", "45")
    monkeypatch.setenv("SCRIBE_INFISICAL_FAIL_FAST", "false")

    policy = InfisicalBootPolicy.from_env()

    assert policy.retry_enabled is False
    assert policy.max_seconds == 120.0
    assert policy.initial_delay_seconds == 3.0
    assert policy.max_delay_seconds == 45.0
    assert policy.fail_fast is False


def test_retry_recovers_once_infisical_returns() -> None:
    """A transient boot outage that clears mid-retry must yield the real overlay
    instead of the degraded fallback (#415)."""
    calls: list[int] = []
    slept: list[float] = []

    def fetch(_keys: object) -> OverlayLoad:
        calls.append(1)
        if len(calls) < 3:
            return OverlayLoad({}, "degraded")
        return OverlayLoad({"public_base_url": "https://recovered.example.test"}, "infisical")

    policy = InfisicalBootPolicy(
        retry_enabled=True,
        max_seconds=300.0,
        initial_delay_seconds=1.0,
        max_delay_seconds=30.0,
        fail_fast=True,
    )

    load = load_infisical_overlay_with_retry(
        Settings.model_fields, policy, fetch=fetch, sleep=slept.append
    )

    assert load.state == "infisical"
    assert load.overlay == {"public_base_url": "https://recovered.example.test"}
    assert len(calls) == 3
    # Bounded exponential backoff: 1s then 2s before the successful third fetch.
    assert slept == [1.0, 2.0]


def test_retry_gives_up_after_budget_exhausted() -> None:
    """Retry is bounded: once the cumulative backoff budget is spent the load
    stays degraded rather than looping forever."""
    calls: list[int] = []

    def fetch(_keys: object) -> OverlayLoad:
        calls.append(1)
        return OverlayLoad({}, "degraded")

    policy = InfisicalBootPolicy(
        retry_enabled=True,
        max_seconds=7.0,
        initial_delay_seconds=2.0,
        max_delay_seconds=30.0,
        fail_fast=True,
    )

    load = load_infisical_overlay_with_retry(
        Settings.model_fields, policy, fetch=fetch, sleep=lambda _s: None
    )

    assert load.state == "degraded"
    # Budget 7s with delays 2,4,(1 capped to remaining) -> 4 fetches total.
    assert len(calls) == 4


def test_build_settings_fail_fast_raises_on_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_module,
        "load_infisical_overlay",
        lambda _keys: OverlayLoad({}, "degraded"),
    )
    policy = InfisicalBootPolicy(
        retry_enabled=False,
        max_seconds=0.0,
        initial_delay_seconds=0.0,
        max_delay_seconds=0.0,
        fail_fast=True,
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        build_settings(policy)

    assert "unreachable" in str(exc_info.value)
    assert runtime_config_load_state.labels(state="degraded")._value.get() == 1.0


def test_build_settings_degraded_env_fallback_logs_when_not_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SCRIBE_PUBLIC_BASE_URL", "https://local.example.test")
    monkeypatch.setattr(
        config_module,
        "load_infisical_overlay",
        lambda _keys: OverlayLoad({}, "degraded"),
    )
    policy = InfisicalBootPolicy(
        retry_enabled=False,
        max_seconds=0.0,
        initial_delay_seconds=0.0,
        max_delay_seconds=0.0,
        fail_fast=False,
    )
    caplog.set_level(logging.ERROR, logger="scribe.runtime_config")

    settings = build_settings(policy)

    assert settings.public_base_url == "https://local.example.test"
    assert "running on env fallback (DEGRADED)" in caplog.text
    assert runtime_config_load_state.labels(state="degraded")._value.get() == 1.0
