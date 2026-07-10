"""Upload staging helpers (#408): filename sanitizing + stage/promote/find/cleanup."""
from __future__ import annotations

import pytest

from scribe.config import settings
from scribe.pipeline import uploads


@pytest.fixture()
def temp_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("clip.mp4", "clip.mp4"),
        ("../../etc/passwd", "passwd"),
        ("my video (1).MP4", "my_video_1_.MP4"),
        ("   ", "upload.bin"),
        ("", "upload.bin"),
        (None, "upload.bin"),
        ("/abs/path/song.mp3", "song.mp3"),
    ],
)
def test_safe_filename(raw, expected):
    assert uploads.safe_filename(raw) == expected


def test_safe_filename_caps_length():
    name = "a" * 500 + ".mp4"
    assert len(uploads.safe_filename(name)) <= 200


def test_stage_promote_find_cleanup_roundtrip(temp_dir):
    staging = uploads.new_staging_path("Movie.mkv")
    assert staging.parent == uploads.uploads_root() / "_staging"
    staging.write_bytes(b"payload")

    final = uploads.promote_to_job(staging, 42, "Movie.mkv")
    assert final == uploads.job_dir(42) / "Movie.mkv"
    assert final.read_bytes() == b"payload"
    # Staging file is moved, not copied.
    assert not staging.exists()

    assert uploads.find_source(42) == final

    uploads.cleanup(42)
    assert uploads.find_source(42) is None
    assert not uploads.job_dir(42).exists()


def test_find_source_none_when_absent(temp_dir):
    assert uploads.find_source(999) is None


def test_discard_staging_is_idempotent(temp_dir):
    staging = uploads.new_staging_path("x.mp4")
    staging.write_bytes(b"x")
    uploads.discard_staging(staging)
    assert not staging.exists()
    # Second discard on a missing file does not raise.
    uploads.discard_staging(staging)
