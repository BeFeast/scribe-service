"""In-process, ephemeral per-job YouTube cookie store (#308 layer B).

The PRD forbids persisting per-job cookies to the database, to disk, or
to logs. Workers run in the same process as the API (see ``main.py``
lifespan), so we hand the validated blob from the request handler to the
worker via this module-level dict, keyed by ``job_id``.

Lifecycle:
- ``stash(job_id, blob)`` is called once after the ``Job`` row is
  committed in the POST /jobs handler.
- ``take(job_id)`` pops the blob when the worker enters the download
  stage and returns ``None`` if nothing was stashed.
- ``discard(job_id)`` is a no-op if absent — used as a safety net in
  failure paths so a job that never reached the download stage does not
  leave a blob in memory.

The blob is never logged and never returned in HTTP responses. Anything
that survives a process restart is intentionally lost: gated jobs that
were queued but not yet downloaded will fail with the standard
public-only error, which matches operator intent.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_jar: dict[int, str] = {}


def stash(job_id: int, blob: str) -> None:
    with _lock:
        _jar[job_id] = blob


def take(job_id: int) -> str | None:
    with _lock:
        return _jar.pop(job_id, None)


def discard(job_id: int) -> None:
    with _lock:
        _jar.pop(job_id, None)


def _peek_for_tests(job_id: int) -> bool:
    with _lock:
        return job_id in _jar
