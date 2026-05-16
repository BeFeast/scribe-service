"""In-process job log tail for SSE consumers."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import deque
from copy import copy
from typing import Any

_MAX_LINES_PER_JOB = 200
_STD_RECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class JobLogBuffer:
    def __init__(self, *, max_lines: int = _MAX_LINES_PER_JOB) -> None:
        self._max_lines = max_lines
        self._lock = threading.Lock()
        self._lines: dict[int, deque[dict[str, Any]]] = {}
        self._versions: dict[int, int] = {}

    def append(self, line: dict[str, Any]) -> None:
        raw_job_id = line.get("job_id")
        if raw_job_id is None:
            return
        try:
            job_id = int(raw_job_id)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._lines.setdefault(
                job_id, deque(maxlen=self._max_lines)
            ).append(line)
            self._versions[job_id] = self._versions.get(job_id, 0) + 1

    def snapshot(self, job_id: int) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            return self._versions.get(job_id, 0), list(self._lines.get(job_id, ()))

    def since(self, job_id: int, version: int) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            current = self._versions.get(job_id, 0)
            if current == version:
                return current, []
            gap = current - version
            lines = list(self._lines.get(job_id, ()))
            return current, lines[-gap:] if 0 < gap < len(lines) else lines

    def discard(self, job_id: int) -> None:
        with self._lock:
            self._lines.pop(job_id, None)
            self._versions.pop(job_id, None)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._versions.clear()


job_log_buffer = JobLogBuffer()


def payload_from_record(record: logging.LogRecord) -> dict[str, Any]:
    copied = copy(record)
    message = copied.getMessage()
    payload: dict[str, Any] = {
        "ts": dt.datetime.fromtimestamp(copied.created, dt.UTC).astimezone().isoformat(timespec="milliseconds"),
        "lvl": copied.levelname,
        "logger": copied.name,
        "msg": message,
    }
    if copied.exc_info:
        payload["exc"] = logging.Formatter().formatException(copied.exc_info)
    for key, value in copied.__dict__.items():
        if key in _STD_RECORD_ATTRS or key.startswith("_"):
            continue
        payload[key] = value
    return payload


class JobLogBufferHandler(logging.Handler):
    """Capture structured worker records with job_id for SSE log tails."""

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("scribe.worker"):
            return
        if not hasattr(record, "job_id"):
            return
        job_log_buffer.append(payload_from_record(record))
