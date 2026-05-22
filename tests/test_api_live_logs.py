"""Tests for live log streaming helpers."""
from __future__ import annotations

import anyio

from scribe.api import routes as routes_module
from scribe.db.models import JobStatus
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


def test_job_log_stream_expires_session_before_terminal_check(monkeypatch):
    job_log_buffer.clear()

    class FakeSession:
        def __init__(self) -> None:
            self.job = type("JobStub", (), {"status": JobStatus.queued})()
            self.expire_calls = 0

        def expire_all(self) -> None:
            self.expire_calls += 1
            self.job.status = JobStatus.done

        def get(self, model, job_id: int):  # noqa: ANN001
            return self.job

    async def no_sleep(_: float) -> None:
        return None

    async def read_stream(session: FakeSession) -> list[str]:
        stream = routes_module._stream_job_logs(123, session)
        try:
            first = await stream.__anext__()
            try:
                await stream.__anext__()
            except StopAsyncIteration:
                return [first]
            raise AssertionError("stream did not stop after terminal status")
        finally:
            await stream.aclose()

    session = FakeSession()
    monkeypatch.setattr(routes_module.asyncio, "sleep", no_sleep)

    assert anyio.run(read_stream, session) == [":\n\n"]
    assert session.expire_calls == 1
    job_log_buffer.clear()
