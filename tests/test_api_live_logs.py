"""Tests for live log streaming helpers."""
from __future__ import annotations

import anyio

from scribe.api import routes as routes_module
from scribe.obs.live_logs import job_log_buffer


def test_job_log_stream_emits_keepalive_when_idle(monkeypatch):
    job_log_buffer.clear()

    async def no_sleep(_: float) -> None:
        return None

    async def read_one() -> str:
        stream = routes_module._stream_job_logs(123)
        try:
            return await stream.__anext__()
        finally:
            await stream.aclose()

    monkeypatch.setattr(routes_module.asyncio, "sleep", no_sleep)

    assert anyio.run(read_one) == ":\n\n"
    job_log_buffer.clear()
