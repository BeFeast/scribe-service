"""Auth policy tests for protected write/operator routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scribe.config import settings
from scribe.main import app

MACHINE_TOKEN = "machine-test-token"


def _external_client(headers: dict[str, str] | None = None) -> TestClient:
    return TestClient(app, headers=headers or {}, client=("203.0.113.10", 50000))


def test_auth_config_remains_public_from_external_ip(monkeypatch):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    client = _external_client()

    config = client.get("/api/auth/config")

    assert config.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/config",
        "/api/prompts",
        "/api/prompts/v1",
        "/api/library",
        "/api/jobs/active",
        "/api/jobs/recent-failures",
        "/api/ops",
        "/admin/backup-status",
        "/admin/daily-report",
        "/jobs/1",
        "/transcripts",
        "/transcripts/1",
        "/transcripts/1/summary.md",
        "/transcripts/1/transcript.md",
    ],
)
def test_external_unauthenticated_app_reads_are_401(monkeypatch, path):
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = _external_client().get(path)

    assert resp.status_code == 401


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


def test_trusted_proxy_xff_drives_cidr_auth(monkeypatch):
    """#348: with a configured trusted proxy, X-Forwarded-For resolves the
    real client IP, so a client on the trusted LAN is allowed while a client
    outside it is not."""
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "trusted_proxies", "172.16.0.0/12")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    # Peer is a trusted proxy; XFF claims a client on the trusted LAN.
    inside = TestClient(app, client=("172.16.0.1", 50000)).post(
        "/api/prompts/active",
        json={"version": "not-a-version"},
        headers={"x-forwarded-for": "10.10.0.5"},
    )
    assert inside.status_code == 422  # trusted -> reached validation

    # Same proxy peer, but XFF client is outside the trusted CIDR.
    outside = TestClient(app, client=("172.16.0.1", 50000)).post(
        "/api/prompts/active",
        json={"version": "v1"},
        headers={"x-forwarded-for": "203.0.113.10"},
    )
    assert outside.status_code == 401


def test_trusted_proxy_walks_past_spoofed_leftmost(monkeypatch):
    """#348: XFF is walked right-to-left skipping trusted proxies, so a client
    cannot gain trust by prepending a fake trusted-LAN entry."""
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "trusted_proxies", "172.16.0.0/12")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    # Spoofed leftmost LAN IP, but the rightmost non-trusted hop is external.
    resp = TestClient(app, client=("172.16.0.1", 50000)).post(
        "/api/prompts/active",
        json={"version": "v1"},
        headers={"x-forwarded-for": "10.10.0.5, 203.0.113.10"},
    )
    assert resp.status_code == 401


def test_no_trusted_proxy_config_ignores_xff(monkeypatch):
    """#348: without trusted_proxies, XFF is not honoured even if the peer is
    a plausible proxy address — the safe default."""
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "trusted_proxies", "")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = TestClient(app, client=("172.16.0.1", 50000)).post(
        "/api/prompts/active",
        json={"version": "v1"},
        headers={"x-forwarded-for": "10.10.0.5"},
    )
    assert resp.status_code == 401


def test_untrusted_peer_with_trusted_proxies_ignores_xff(monkeypatch):
    """#348: if the immediate peer is not a configured proxy, XFF is ignored
    even with trusted_proxies set, so a direct external client cannot spoof."""
    monkeypatch.setattr(settings, "trusted_cidrs", "10.10.0.0/16")
    monkeypatch.setattr(settings, "trusted_proxies", "172.16.0.0/12")
    monkeypatch.setattr(settings, "machine_bearer_token", MACHINE_TOKEN)
    resp = TestClient(app, client=("198.51.100.7", 50000)).post(
        "/api/prompts/active",
        json={"version": "v1"},
        headers={"x-forwarded-for": "10.10.0.5"},
    )
    assert resp.status_code == 401
