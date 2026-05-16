"""Structured JSON logging for scribe.

Drops in over stdlib `logging`. Call `configure()` once at process startup
(see main.py lifespan) — every subsequent `logging.getLogger(...).info(...)`
emits a single JSON line with a stable shape:

    {"ts": "...", "lvl": "INFO", "logger": "scribe.worker", "msg": "...", "job_id": 7, "stage": "whisper"}

Per-call structured fields go through `extra={"job_id": ..., "stage": ...}`.
Anything passed in `extra` and not part of the LogRecord defaults is merged
into the JSON object.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from scribe.obs.live_logs import JobLogBufferHandler, payload_from_record


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = payload_from_record(record)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(level: str | int | None = None) -> None:
    """Idempotent root-logger setup. Honours SCRIBE_LOG_LEVEL env."""
    level = level or os.environ.get("SCRIBE_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = level.upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # Replace existing handlers — basicConfig may already have installed one.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.addHandler(JobLogBufferHandler())
    root.setLevel(level)
    # Quiet uvicorn's access log noise; access events go through the same
    # handler but at WARNING by default. Flip to INFO via env if needed.
    logging.getLogger("uvicorn.access").setLevel(
        os.environ.get("SCRIBE_UVICORN_ACCESS_LEVEL", "WARNING").upper()
    )
