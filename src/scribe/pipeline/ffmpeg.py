"""ffmpeg audio normalization — single pass to 16 kHz mono wav for whisper.

Input may be any audio or video container (yt-dlp's `ba/best[height<=360]/18`
selector can yield m4a/webm/opus audio or, as a fallback, a small video file).
`-vn` drops any video stream; `-ar 16000 -ac 1` is what faster-whisper wants.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def to_wav_16k_mono(src: Path, dest: Path) -> Path:
    """Resample `src` to a 16 kHz mono wav at `dest`. Returns `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg failed (rc={proc.returncode}):\n{proc.stderr[-1500:]}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise FfmpegError(f"ffmpeg produced no output at {dest}")
    return dest
