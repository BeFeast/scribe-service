"""Auth policy tests for protected write/operator routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scribe.config import settings
from scribe.main import app

MACHINE_TOKEN = "machine-test-token"


def _external_client(headers: dict[str, str] | None = None) -> TestClient:
    return TestClient(app, headers=headers or {}, client=("203.0.113.10", 50000))


def test_public_reads_work_without_auth_from_external_ip(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    client = _external_client()

    prompts = client.get("/api/prompts")
    config = client.get("/api/config")

    assert prompts.status_code == 200
    assert config.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/jobs", {"json": {"url": "https://youtu.be/dQw4w9WgXcQ"}}),
        ("post", "/api/config", {"json": {"daily_spend_cap_usd": 1.0}}),
        ("post", "/api/prompts/active", {"json": {"version": "v1"}}),
        ("post", "/admin/jobs/1/cancel", {}),
        ("post", "/admin/jobs/1/retry", {}),
        ("delete", "/admin/jobs/1", {}),
        ("delete", "/admin/transcripts/1", {}),
        ("post", "/transcripts/1/resummarize", {"headers": {"Accept": "application/json"}}),
    ],
)
def test_external_unauthenticated_writes_are_401(monkeypatch, method, path, kwargs):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = getattr(_external_client(), method)(path, **kwargs)

    assert resp.status_code == 401


def test_trusted_cidr_write_is_allowed_without_bearer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "203.0.113.0/24")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = _external_client().post("/api/prompts/active", json={"version": "not-a-version"})

    assert resp.status_code == 422


def test_machine_bearer_write_is_allowed(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = _external_client({"Authorization": f"Bearer {MACHINE_TOKEN}"}).post(
        "/api/prompts/active",
        json={"version": "not-a-version"},
    )

    assert resp.status_code == 422


def test_bad_machine_bearer_is_401(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = _external_client({"Authorization": "Bearer wrong-token"}).post(
        "/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert resp.status_code == 401


def test_malformed_authorization_header_is_401(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "203.0.113.0/24")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = _external_client({"Authorization": "Bearer"}).post(
        "/jobs",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    assert resp.status_code == 401
