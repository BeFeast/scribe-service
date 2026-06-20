"""FastAPI entrypoint. `uvicorn scribe.main:app`.

The app's lifespan starts the in-process job-queue workers (see worker/loop.py),
so a single `uvicorn scribe.main:app` serves the API + web-UI and processes jobs.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from scribe import __version__
from scribe.api.routes import router as api_router
from scribe.config import settings
from scribe.obs import ops as ops_helpers
from scribe.obs.correlation import HEADER, request_correlation_id
from scribe.obs.logging import configure as configure_logging
from scribe.web.views import router as web_router
from scribe.worker.download_canary import run_download_canary_loop
from scribe.worker.loop import start_workers
from scribe.worker.vast_budget import start_budget_monitor
from scribe.worker.vast_reaper import run_vast_reaper_loop

# Structured JSON logging — replaces basicConfig. Honours SCRIBE_LOG_LEVEL.
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("scribe")
    try:
        settings.runtime_overlay()
    except (SQLAlchemyError, ValueError):
        log.exception("runtime config overlay failed")
    if settings.auth_test_mode:
        log.warning("auth test mode is enabled; test identity headers can impersonate local users")
    threads = []
    stop = None
    reaper_task: asyncio.Task | None = None
    canary_task: asyncio.Task | None = None
    budget_stop = None
    if not settings.app_start_workers or "PYTEST_CURRENT_TEST" in os.environ:
        log.info("workers disabled", extra={"pytest": "PYTEST_CURRENT_TEST" in os.environ})
    else:
        threads, stop = start_workers()
        reaper_task = asyncio.create_task(run_vast_reaper_loop(), name="scribe-vast-orphan-reaper")
        canary_task = asyncio.create_task(run_download_canary_loop(), name="scribe-download-canary")
        budget_thread, budget_stop = start_budget_monitor()
        threads.append(budget_thread)
        log.info("workers started", extra={"thread_count": len(threads)})
        log.info("vast budget monitor started")
        log.info("download canary started")
    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass
        if canary_task is not None:
            canary_task.cancel()
            try:
                await canary_task
            except asyncio.CancelledError:
                pass
        if budget_stop is not None:
            budget_stop.set()
        if stop is not None:
            stop.set()
        for thread in threads:
            thread.join(timeout=2.0)


app = FastAPI(title="scribe", version=__version__, lifespan=lifespan)
app.mount(
    "/static/spa",
    StaticFiles(
        directory=Path(__file__).parent / "web" / "static" / "spa",
        check_dir=False,
    ),
    name="spa-static",
)
app.include_router(api_router)
app.include_router(web_router)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):  # noqa: D401, ANN001
    """Stamp every request with a correlation ID and echo it in the response.

    Honours inbound ``X-Request-ID`` (otherwise generates one); stores the
    value on ``request.state`` for routes to read and sets it on the response
    header so callers can correlate (#357)."""
    correlation_id = request_correlation_id(request)
    response = await call_next(request)
    response.headers[HEADER] = correlation_id
    return response


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    """Fast liveness probe — the process is up and serving. Unconditional `ok`
    so orchestrators only restart on crash/restart, not on a flaky dependency.
    Use `/readyz` for the deep readiness check that verifies subsystems."""
    return {"status": "ok", "service": "scribe", "version": app.version}


@app.get("/readyz", tags=["ops"])
def readyz(response: Response) -> dict:
    """Deep readiness probe — verifies the critical subsystems (Postgres,
    Vast, codex, backup heartbeat) by reusing the `obs/ops.py` probes.

    Returns 200 only when every required subsystem reports `ok`; 503 with a
    per-subsystem breakdown otherwise. The fast `/healthz` liveness behaviour
    is preserved — this endpoint holds no probe logic of its own."""
    subsystems = ops_helpers.readiness_subsystems()
    ok = all(item["status"] == "ok" for item in subsystems.values())
    response.status_code = 200 if ok else 503
    return {
        "status": "ok" if ok else "degraded",
        "service": "scribe",
        "version": app.version,
        "subsystems": subsystems,
    }
