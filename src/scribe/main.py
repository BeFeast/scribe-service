"""FastAPI entrypoint. `uvicorn scribe.main:app`.

The app's lifespan starts the in-process job-queue workers (see worker/loop.py),
so a single `uvicorn scribe.main:app` serves the API + web-UI and processes jobs.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scribe.api.routes import router as api_router
from scribe.web.views import router as web_router
from scribe.worker.loop import start_workers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    threads, stop = start_workers()
    logging.getLogger("scribe").info("started %d worker thread(s)", len(threads))
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title="scribe", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(web_router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {"status": "ok", "service": "scribe", "version": app.version}
