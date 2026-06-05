"""/healthz must report the installed package version (deploy-truth).

The release pipeline asserts `/healthz .version == released tag`, so the
running version has to track `pyproject.toml` via package metadata with no
source edit.
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version

from fastapi.testclient import TestClient

from scribe.main import app


def test_healthz_version_matches_package_metadata() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["version"] == pkg_version("scribe")
