"""ffmpeg audio normalization — single pass to 16 kHz mono wav for whisper.

Input may be any audio or video container (yt-dlp's `ba/best[height<=360]/18`
selector can yield m4a/webm/opus audio or, as a fallback, a small video file).
`-vn` drops any video stream; `-ar 16000 -ac 1` is what faster-whisper wants.

For uploaded sources (#408) this module also validates the upload with ffprobe
before it enters the pipeline and transcodes the downscaled archival copy that
is stored in R2 — a 480p H.264/AAC mp4 for video, an Opus file for audio-only.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
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


@dataclass(frozen=True)
class MediaProbe:
    """Result of ffprobe on an uploaded source (#408)."""

    has_video: bool
    has_audio: bool
    duration_seconds: int | None


def probe_media(src: Path) -> MediaProbe:
    """Validate `src` is a real media file and report its stream shape.

    Raises :class:`FfmpegError` when ffprobe fails or the file has neither an
    audio nor a (non-cover-art) video stream — this is the pre-transcribe gate
    that rejects corrupt/non-media uploads with a clear error. Cover-art image
    streams (``disposition.attached_pic``) are ignored so an mp3 with embedded
    album art is treated as audio-only, not video.
    """
    proc = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_streams", "-show_format", str(src),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe failed (rc={proc.returncode}):\n{proc.stderr[-1500:]}")
    try:
        info = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FfmpegError("ffprobe returned invalid JSON") from exc

    has_video = False
    has_audio = False
    for stream in info.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            has_audio = True
        elif codec_type == "video":
            # Skip attached cover art / thumbnails so audio-only files with
            # embedded artwork are not mistaken for video.
            if not (stream.get("disposition") or {}).get("attached_pic"):
                has_video = True

    if not has_audio and not has_video:
        raise FfmpegError("upload has no decodable audio or video stream")

    duration_seconds: int | None = None
    raw_duration = (info.get("format") or {}).get("duration")
    try:
        if raw_duration is not None:
            duration_seconds = int(float(raw_duration))
    except (TypeError, ValueError):
        duration_seconds = None

    return MediaProbe(has_video=has_video, has_audio=has_audio, duration_seconds=duration_seconds)


def transcode_archival_video(src: Path, dest: Path) -> Path:
    """Transcode `src` to a 480p H.264/AAC mp4 archival copy at `dest` (#408).

    ~200-350 MB/h. `-vf scale=-2:480` caps height at 480p (width auto, kept
    even for H.264). `+faststart` moves the moov atom to the front so the R2
    object streams/seeks over HTTP.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-vf", "scale=-2:480",
            "-c:v", "libx264", "-crf", "28", "-preset", "slow",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FfmpegError(f"archival video transcode failed (rc={proc.returncode}):\n{proc.stderr[-1500:]}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise FfmpegError(f"archival video transcode produced no output at {dest}")
    return dest


def transcode_archival_audio(src: Path, dest: Path) -> Path:
    """Transcode `src` to a low-bitrate Opus archival copy at `dest` (#408).

    Audio-only uploads skip the video stage entirely; 40 kbps Opus keeps voice
    intelligible at a tiny footprint.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn",
            "-c:a", "libopus", "-b:a", "40k",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FfmpegError(f"archival audio transcode failed (rc={proc.returncode}):\n{proc.stderr[-1500:]}")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise FfmpegError(f"archival audio transcode produced no output at {dest}")
    return dest
