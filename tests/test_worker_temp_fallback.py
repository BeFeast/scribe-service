"""Worker temp-dir fallback (issue #379).

When ``settings.temp_dir`` exists but is not writable by the worker user,
``mkdir(parents=True, exist_ok=True)`` succeeds (the dir is already there) yet
``tempfile.mkdtemp(dir=temp_dir)`` raises ``PermissionError`` — previously this
crashed the Download stage. ``_make_job_tmpdir`` must instead degrade to the
system temp dir and log a warning, while a writable ``temp_dir`` keeps its old
behaviour. Tested at the helper level so no DB is required.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from scribe.worker import loop as worker_loop


def _job_log() -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger("scribe.worker"), {"job_id": 1})


def test_unwritable_temp_dir_falls_back_to_system_temp(monkeypatch, tmp_path, caplog):
    # Directory exists (so mkdir(exist_ok=True) succeeds) but mkdtemp fails on it,
    # exactly like a /data/tmp owned by another user.
    unwritable = tmp_path / "data-tmp"
    unwritable.mkdir()

    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(*args, **kwargs):
        if kwargs.get("dir") is not None and Path(kwargs["dir"]) == unwritable:
            raise PermissionError(13, "Permission denied")
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(worker_loop.tempfile, "mkdtemp", fake_mkdtemp)

    with caplog.at_level(logging.WARNING, logger="scribe.worker"):
        tmpdir = worker_loop._make_job_tmpdir(str(unwritable), _job_log())

    try:
        # Landed under the system temp dir, not the unwritable configured dir.
        system_temp = Path(tempfile.gettempdir())
        assert system_temp in tmpdir.parents
        assert unwritable not in tmpdir.parents
        assert tmpdir.is_dir()

        # A warning naming the original temp_dir was emitted in the message text
        # (the structured LoggerAdapter drops call-site extra keys, so the message
        # is what actually reaches the log).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "falling back to system temp" in r.getMessage() and str(unwritable) in r.getMessage()
            for r in warnings
        )
    finally:
        tmpdir.rmdir()


def test_missing_temp_dir_parent_falls_back(monkeypatch, tmp_path, caplog):
    # A bogus temp_dir whose parent is itself non-writable: mkdir raises too.
    # mkdtemp is left real here; gettempdir() supplies a usable fallback.
    bogus = tmp_path / "nope"

    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(*args, **kwargs):
        if kwargs.get("dir") is not None and Path(kwargs["dir"]) == bogus:
            raise FileNotFoundError(2, "No such file or directory")
        return real_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(worker_loop.tempfile, "mkdtemp", fake_mkdtemp)
    # Force the mkdir path to fail with PermissionError so we exercise the guard
    # even when the test user could otherwise create the directory.
    real_mkdir = Path.mkdir

    def fake_mkdir(self, *a, **k):
        if self == bogus:
            raise PermissionError(13, "Permission denied")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    with caplog.at_level(logging.WARNING, logger="scribe.worker"):
        tmpdir = worker_loop._make_job_tmpdir(str(bogus), _job_log())

    try:
        assert Path(tempfile.gettempdir()) in tmpdir.parents
        assert any("falling back to system temp" in r.getMessage() for r in caplog.records)
    finally:
        tmpdir.rmdir()


def test_writable_temp_dir_behaviour_unchanged(tmp_path, caplog):
    writable = tmp_path / "data-tmp"
    writable.mkdir()

    with caplog.at_level(logging.WARNING, logger="scribe.worker"):
        tmpdir = worker_loop._make_job_tmpdir(str(writable), _job_log())

    try:
        # tmpdir was created under the configured temp_dir, no fallback triggered.
        assert writable in tmpdir.parents
        assert tmpdir.name.startswith("scribe-job-")
        assert not any("falling back to system temp" in r.getMessage() for r in caplog.records)
    finally:
        tmpdir.rmdir()


def test_creates_missing_writable_temp_dir(tmp_path):
    # temp_dir does not exist yet but its parent is writable: mkdir creates it,
    # mkdtemp succeeds, no fallback.
    target = tmp_path / "data" / "tmp"

    tmpdir = worker_loop._make_job_tmpdir(str(target), _job_log())
    try:
        assert target in tmpdir.parents
        assert target.is_dir()
    finally:
        tmpdir.rmdir()
