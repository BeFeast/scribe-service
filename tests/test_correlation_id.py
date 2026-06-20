"""API ingress correlation ID middleware (#357).

Pure HTTP tests: the middleware honours an inbound X-Request-ID, generates
one when absent, and echoes the resolved value back in the response header
on every route (including ops endpoints that do not create jobs).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from scribe.main import app
from scribe.obs.correlation import HEADER, new_correlation_id, request_correlation_id


class _DummyRequest:
    """Minimal stand-in for starlette.Request used by request_correlation_id."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers
        self.state = type("state", (), {})()


def test_healthz_echoes_inbound_request_id():
    client = TestClient(app)
    resp = client.get("/healthz", headers={HEADER: "trace-from-caller"})
    assert resp.status_code == 200
    assert resp.headers[HEADER] == "trace-from-caller"


def test_healthz_generates_request_id_when_absent():
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    echoed = resp.headers[HEADER]
    assert echoed
    # Generated, not empty; and stable shape (opaque token).
    assert echoed != ""


def test_request_correlation_id_honours_inbound_then_cached():
    req = _DummyRequest({HEADER: "caller-123"})
    assert request_correlation_id(req) == "caller-123"
    # Cached on state so a second read within the same request is stable.
    assert request_correlation_id(req) == "caller-123"


def test_request_correlation_id_generates_when_absent():
    req = _DummyRequest({})
    value = request_correlation_id(req)
    assert value
    assert value == request_correlation_id(req)
    # Distinct calls produce distinct ids.
    other = new_correlation_id()
    assert other != value


def test_request_correlation_id_rejects_oversized_inbound():
    # An absurdly long inbound header must not be trusted; a fresh id is
    # generated instead so log lines / downstream headers stay bounded.
    req = _DummyRequest({HEADER: "x" * 10_000})
    value = request_correlation_id(req)
    assert value != "x" * 10_000
    assert len(value) <= 128


def test_request_correlation_id_rejects_blank_inbound():
    req = _DummyRequest({HEADER: "   "})
    value = request_correlation_id(req)
    assert value
    assert value.strip() != ""
