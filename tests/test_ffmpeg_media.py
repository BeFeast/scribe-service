"""Real ffprobe/ffmpeg validation + archival transcode (#408).

Exercises the actual binaries against tiny synthetic media generated with
ffmpeg's lavfi sources. Skipped when ffmpeg/ffprobe are not on PATH.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from scribe.pipeline import ffmpeg

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _make_video(path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-shortest", str(path),
        ],
        check=True,
    )


def _make_audio(path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            str(path),
        ],
        check=True,
    )


def test_probe_video_has_video_and_audio(tmp_path):
    src = tmp_path / "clip.mp4"
    _make_video(src)
    probe = ffmpeg.probe_media(src)
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.duration_seconds == 1


def test_probe_audio_only(tmp_path):
    src = tmp_path / "clip.mp3"
    _make_audio(src)
    probe = ffmpeg.probe_media(src)
    assert probe.has_video is False
    assert probe.has_audio is True


def test_probe_rejects_non_media(tmp_path):
    src = tmp_path / "notmedia.bin"
    src.write_bytes(b"this is definitely not a media file" * 10)
    with pytest.raises(ffmpeg.FfmpegError):
        ffmpeg.probe_media(src)


def test_transcode_archival_video(tmp_path):
    src = tmp_path / "clip.mp4"
    _make_video(src)
    out = tmp_path / "archive.mp4"
    ffmpeg.transcode_archival_video(src, out)
    assert out.is_file() and out.stat().st_size > 0
    # Output is real, probeable media with a video stream.
    assert ffmpeg.probe_media(out).has_video is True


def test_transcode_archival_audio(tmp_path):
    src = tmp_path / "clip.mp3"
    _make_audio(src)
    out = tmp_path / "archive.opus"
    ffmpeg.transcode_archival_audio(src, out)
    assert out.is_file() and out.stat().st_size > 0
    probe = ffmpeg.probe_media(out)
    assert probe.has_audio is True
    assert probe.has_video is False
