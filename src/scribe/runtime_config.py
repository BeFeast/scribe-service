"""Infisical-backed startup configuration overlay."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import httpx

DEFAULT_INFISICAL_API_URL = "https://us.infisical.com"
DEFAULT_INFISICAL_PROJECT = "services"
DEFAULT_INFISICAL_ENVIRONMENT = "prod"
DEFAULT_INFISICAL_PATH = "/scribe-service"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class InfisicalConfig:
    enabled: bool
    api_url: str
    client_id: str
    client_secret: str
    organization_slug: str
    project: str
    environment: str
    path: str
    timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> InfisicalConfig:
        source = env if env is not None else os.environ
        client_id = _first_env(
            source,
            "SCRIBE_INFISICAL_CLIENT_ID",
            "INFISICAL_UNIVERSAL_AUTH_CLIENT_ID",
            "INFISICAL_CLIENT_ID",
        )
        client_secret = _first_env(
            source,
            "SCRIBE_INFISICAL_CLIENT_SECRET",
            "INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET",
            "INFISICAL_CLIENT_SECRET",
        )
        enabled = _bool_env(source.get("SCRIBE_INFISICAL_ENABLED"), bool(client_id and client_secret))
        return cls(
            enabled=enabled,
            api_url=_first_env(source, "SCRIBE_INFISICAL_API_URL", "INFISICAL_API_URL")
            or DEFAULT_INFISICAL_API_URL,
            client_id=client_id,
            client_secret=client_secret,
            organization_slug=_first_env(
                source,
                "SCRIBE_INFISICAL_ORGANIZATION_SLUG",
                "INFISICAL_ORGANIZATION_SLUG",
            ),
            project=source.get("SCRIBE_INFISICAL_PROJECT", DEFAULT_INFISICAL_PROJECT),
            environment=source.get("SCRIBE_INFISICAL_ENVIRONMENT", DEFAULT_INFISICAL_ENVIRONMENT),
            path=source.get("SCRIBE_INFISICAL_PATH", DEFAULT_INFISICAL_PATH),
            timeout_seconds=float(source.get("SCRIBE_INFISICAL_TIMEOUT_SECONDS", "5")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.client_id and self.client_secret)


class RuntimeConfigError(RuntimeError):
    """Sanitized Infisical runtime-config failure."""


def load_infisical_settings(
    allowed_keys: Iterable[str],
    *,
    config: InfisicalConfig | None = None,
    client: httpx.Client | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, str]:
    """Load a sanitized Settings overlay from Infisical.

    Returns an empty mapping when Infisical is disabled, not configured, or
    unavailable. Returned keys are pydantic Settings field names.
    """
    cfg = config or InfisicalConfig.from_env()
    if not cfg.configured:
        return {}

    log = logger or logging.getLogger("scribe.runtime_config")
    owns_client = client is None
    http = client or httpx.Client(base_url=cfg.api_url.rstrip("/"), timeout=cfg.timeout_seconds)
    try:
        token = _login(http, cfg)
        project_id = _project_id(http, token, cfg.project)
        raw = _secrets(http, token, cfg, project_id)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeConfigError) as exc:
        message = redact_text(str(exc), cfg)
        log.warning("infisical runtime config unavailable; using env fallback", extra={"error": message})
        return {}
    finally:
        if owns_client:
            http.close()

    overlay = _normalize_settings(raw, allowed_keys)
    log.info(
        "infisical runtime config loaded",
        extra={
            "project": cfg.project,
            "environment": cfg.environment,
            "path": cfg.path,
            "key_count": len(overlay),
        },
    )
    return overlay


def redact_text(value: str, config: InfisicalConfig | None = None) -> str:
    """Return text safe for logs and exceptions."""
    redacted = value
    candidates = []
    if config is not None:
        candidates.extend((config.client_id, config.client_secret))
    for candidate in candidates:
        if candidate:
            redacted = redacted.replace(candidate, "[redacted]")
    return redacted


def redact_values(value: str, secrets: Iterable[str]) -> str:
    """Redact known secret values from arbitrary text."""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _login(client: httpx.Client, cfg: InfisicalConfig) -> str:
    payload: dict[str, str] = {"clientId": cfg.client_id, "clientSecret": cfg.client_secret}
    if cfg.organization_slug:
        payload["organizationSlug"] = cfg.organization_slug
    response = client.post("/api/v1/auth/universal-auth/login", json=payload)
    response.raise_for_status()
    token = response.json()["accessToken"]
    if not isinstance(token, str) or not token:
        raise RuntimeConfigError("Infisical login returned an empty access token")
    return token


def _project_id(client: httpx.Client, token: str, project: str) -> str:
    if _looks_like_project_id(project):
        return project
    response = client.get(
        f"/api/v1/projects/slug/{project}",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    project_id = response.json()["id"]
    if not isinstance(project_id, str) or not project_id:
        raise RuntimeConfigError("Infisical project lookup returned an empty project id")
    return project_id


def _secrets(
    client: httpx.Client,
    token: str,
    cfg: InfisicalConfig,
    project_id: str,
) -> dict[str, str]:
    response = client.get(
        "/api/v4/secrets",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "projectId": project_id,
            "environment": cfg.environment,
            "secretPath": cfg.path,
            "viewSecretValue": "true",
            "expandSecretReferences": "true",
            "includeImports": "true",
        },
    )
    response.raise_for_status()
    body = response.json()
    secrets = body.get("secrets", [])
    if not isinstance(secrets, list):
        raise RuntimeConfigError("Infisical secrets response was malformed")
    return {
        item["secretKey"]: item["secretValue"]
        for item in secrets
        if isinstance(item, dict)
        and isinstance(item.get("secretKey"), str)
        and isinstance(item.get("secretValue"), str)
    }


def _normalize_settings(raw: Mapping[str, str], allowed_keys: Iterable[str]) -> dict[str, str]:
    allowed = set(allowed_keys)
    overlay: dict[str, str] = {}
    for key, value in raw.items():
        normalized = _settings_key(key)
        if normalized in allowed:
            overlay[normalized] = value
    return overlay


def _settings_key(key: str) -> str:
    normalized = key.strip().lower()
    if normalized.startswith("scribe_"):
        normalized = normalized.removeprefix("scribe_")
    return normalized


def _first_env(source: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = source.get(key, "").strip()
        if value:
            return value
    return ""


def _bool_env(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def _looks_like_project_id(value: str) -> bool:
    return len(value) >= 20 and "-" in value
