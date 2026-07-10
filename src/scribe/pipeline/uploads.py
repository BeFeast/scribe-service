"""Local staging for user-uploaded media (#408).

Uploaded video/audio is streamed to ``temp_dir/uploads/`` so it survives the
API handler -> in-process worker handoff without buffering the payload in
memory (unlike the cookie_jar pattern used for the tiny cookie blob). Each
accepted upload lands in a per-job directory ``temp_dir/uploads/<job_id>/`` so
the worker can locate it by job id, feed it to the transcribe pipeline, and —
after archiving the downscaled copy to R2 — delete the directory.

The staging file is written under ``temp_dir/uploads/_staging/`` first, because
the content SHA-256 (which forms the dedup key ``upload:<sha16>``) is only known
after the whole body has been read, and the ``Job`` row — hence its id — is not
created until after the dedup check. Once the job exists the staged file is
moved into its per-job directory.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from scribe.config import settings

# 1 MiB streaming chunk — never load the whole upload into memory.
CHUNK_SIZE = 1024 * 1024

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_NAME = "upload.bin"
_MAX_NAME_LEN = 200


class UploadTooLargeError(RuntimeError):
    """Raised when a streamed upload exceeds the configured byte ceiling."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"upload exceeds {limit} byte limit")
        self.limit = limit


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    size_bytes: int
    sha256: str


def uploads_root() -> Path:
    return Path(settings.temp_dir) / "uploads"


def _staging_root() -> Path:
    return uploads_root() / "_staging"


def job_dir(job_id: int) -> Path:
    return uploads_root() / str(job_id)


def safe_filename(name: str | None) -> str:
    """Sanitize a client-supplied filename to a safe basename.

    Strips any directory component, collapses unsafe characters to ``_``, and
    caps length. Never returns an empty string.
    """
    base = os.path.basename((name or "").strip())
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._")
    if not cleaned:
        return _DEFAULT_NAME
    return cleaned[:_MAX_NAME_LEN]


def new_staging_path(filename: str | None) -> Path:
    """Create the staging dir and return a unique path for a streamed upload."""
    root = _staging_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid.uuid4().hex}-{safe_filename(filename)}"


def discard_staging(path: Path) -> None:
    """Delete a staged file (best-effort). Used on oversize/validation reject."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def promote_to_job(staging_path: Path, job_id: int, filename: str | None) -> Path:
    """Move a staged upload into ``temp_dir/uploads/<job_id>/`` and return it.

    A fresh per-job directory is created (any stale leftover is removed first)
    so the worker's ``find_source`` reliably finds exactly one file.
    """
    dest_dir = job_dir(job_id)
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(filename)
    shutil.move(str(staging_path), str(dest))
    return dest


def find_source(job_id: int) -> Path | None:
    """Return the uploaded source file for a job, or None if absent.

    Returns the single regular file inside ``temp_dir/uploads/<job_id>/``.
    """
    dest_dir = job_dir(job_id)
    if not dest_dir.is_dir():
        return None
    for entry in sorted(dest_dir.iterdir()):
        if entry.is_file():
            return entry
    return None


def cleanup(job_id: int) -> None:
    """Remove a job's upload directory (best-effort)."""
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
