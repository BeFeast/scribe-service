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

import datetime as dt
import json
import logging
import os
import sys

# Fields that LogRecord sets natively. Everything else passed via `extra`
# is treated as a structured field and merged into the JSON object.
_STD_RECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        message = record.getMessage()
        payload: dict[str, object] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": message,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
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
    root.setLevel(level)
    # Quiet uvicorn's access log noise; access events go through the same
    # handler but at WARNING by default. Flip to INFO via env if needed.
    logging.getLogger("uvicorn.access").setLevel(
        os.environ.get("SCRIBE_UVICORN_ACCESS_LEVEL", "WARNING").upper()
    )
