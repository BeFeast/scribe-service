"""FastAPI entrypoint. `uvicorn scribe.main:app`."""
from fastapi import FastAPI

from scribe.api.routes import router as api_router
from scribe.web.views import router as web_router

app = FastAPI(title="scribe", version="0.1.0")
app.include_router(api_router)
app.include_router(web_router)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    # TODO(task#9): report bgutil/vast-key/codex-auth status
    return {"status": "ok", "service": "scribe"}
