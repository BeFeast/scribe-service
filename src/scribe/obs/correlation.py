"""Request correlation ID plumbing (#357).

A single correlation ID links one job across submission -> download ->
whisper -> summary -> webhook so tracing one job does not require manual
log stitching.

- API ingress honours an inbound ``X-Request-ID`` header; otherwise a fresh
  ID is generated.
- The ID is stored on the Job row and included in every structured log line
  for that job (worker LoggerAdapter) and in the webhook ``X-Request-ID``
  delivery header.
- The same ID is echoed back in every API response header.
"""
from __future__ import annotations

import secrets
from typing import Final

from fastapi import Request

HEADER: Final[str] = "X-Request-ID"
# Bounded length keeps log lines / headers sane and rejects absurd inbound
# values. 128 chars is generous for any sane trace ID format (W3C traceparent
# is 55, UUID4 hex is 32, etc.).
_MAX_LEN: Final[int] = 128


def new_correlation_id() -> str:
    """Generate a fresh opaque correlation ID."""
    return secrets.token_urlsafe(12)


def request_correlation_id(request: Request) -> str:
    """Return the correlation ID for this request.

    Honours an inbound ``X-Request-ID`` when present and reasonable (non-empty
    and within the length cap); otherwise generates a fresh one. The resolved
    value is cached on ``request.state`` so a single request observes one
    stable ID across handlers/middleware.
    """
    cached = getattr(request.state, "correlation_id", None)
    if cached:
        return cached
    inbound = request.headers.get(HEADER, "").strip()
    if inbound and len(inbound) <= _MAX_LEN:
        value = inbound
    else:
        value = new_correlation_id()
    request.state.correlation_id = value
    return value
