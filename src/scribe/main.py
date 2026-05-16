"""FastAPI entrypoint. `uvicorn scribe.main:app`.

The app's lifespan starts the in-process job-queue workers (see worker/loop.py),
so a single `uvicorn scribe.main:app` serves the API + web-UI and processes jobs.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from scribe.api.routes import router as api_router
from scribe.config import settings
from scribe.obs.logging import configure as configure_logging
from scribe.web.views import router as web_router
from scribe.worker.loop import start_workers

# Structured JSON logging — replaces basicConfig. Honours SCRIBE_LOG_LEVEL.
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("scribe")
    try:
        settings.runtime_overlay()
    except SQLAlchemyError:
        log.exception("runtime config overlay failed")
    threads, stop = start_workers()
    log.info("workers started", extra={"thread_count": len(threads)})
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title="scribe", version="0.1.0", lifespan=lifespan)
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


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {"status": "ok", "service": "scribe", "version": app.version}
