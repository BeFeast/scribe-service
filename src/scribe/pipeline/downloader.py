"""yt-dlp downloader — runs locally from a residential IP.

Ported from run_vast_video_summary.py's remote_shell_script. The "Sign in to
confirm you're not a bot" wall the Vast pipeline fought is an IP-reputation gate
on datacenter IPs; from a residential IP it is structurally absent, so the
bot-wall retry here is cheap insurance only. No bgutil PO token provider needed
(android_vr in the chain is token-free).

This module only *downloads* the raw audio stream — no `-x`/ffmpeg postprocessing.
Resampling to 16 kHz mono wav is a separate single ffmpeg pass (see ffmpeg.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# yt-dlp tries these clients in order. android_vr is the token-free workhorse;
# the web clients use EJS + deno for JS-challenge solving.
PLAYER_CLIENTS = "mweb,web_safari,android_vr,web_embedded"
BOTWALL_RE = re.compile(r"Sign in to confirm|LOGIN_REQUIRED|not a bot", re.IGNORECASE)
MAX_TRIES = 3
BACKOFF_SECONDS = 45
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})")
_NON_KEY_CHARS_RE = re.compile(r"[^a-z0-9_.-]+")


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    video_id: str
    duration_seconds: int | None


def parse_youtube_video_id(url: str) -> str | None:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


def extract_video_id(url: str) -> str:
    match = parse_youtube_video_id(url)
    if not match:
        raise DownloadError(f"could not parse a YouTube video id from: {url}")
    return match


def initial_video_key(url: str) -> str:
    youtube_id = parse_youtube_video_id(url)
    if youtube_id is not None:
        return youtube_id
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:24]
    return f"pending:{digest}"


def normalized_video_key(extractor: str | None, media_id: str | None) -> str | None:
    if not media_id:
        return None
    provider = _NON_KEY_CHARS_RE.sub("-", (extractor or "").strip().lower()).strip("-")
    if provider in {"youtube", "youtube-tab", "youtube-playlist"}:
        return media_id
    if not provider:
        return media_id
    return f"{provider}:{media_id}"


def _base_args(deno_path: str) -> list[str]:
    return [
        "yt-dlp",
        "--no-playlist",
        "--remote-components", "ejs:github",
        "--js-runtimes", f"deno:{deno_path}",
        "--extractor-args", f"youtube:player_client={PLAYER_CLIENTS}",
        "--sleep-requests", "1",
        "--min-sleep-interval", "1",
        "--max-sleep-interval", "3",
    ]


def _run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp; retry on the YouTube bot-wall signature."""
    last: subprocess.CompletedProcess | None = None
    for attempt in range(1, MAX_TRIES + 1):
        last = subprocess.run(args, capture_output=True, text=True)
        if last.returncode == 0:
            return last
        if BOTWALL_RE.search(last.stderr or "") and attempt < MAX_TRIES:
            time.sleep(BACKOFF_SECONDS)
            continue
        break
    detail = (last.stderr or last.stdout or "")[-2000:]
    raise DownloadError(f"video extraction failed (yt-dlp rc={last.returncode}):\n{detail}")


def download_audio(url: str, dest_dir: Path, *, deno_path: str = "deno") -> DownloadResult:
    """Download the audio stream of `url` into `dest_dir`, return metadata + path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = _base_args(deno_path)

    meta = _run_ytdlp([*base, "--skip-download", "--dump-single-json", url])
    try:
        info = json.loads(meta.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise DownloadError("video extraction failed: yt-dlp returned invalid metadata") from exc
    video_id = normalized_video_key(info.get("extractor_key"), info.get("id")) or initial_video_key(url)
    title = info.get("title") or url
    duration = info.get("duration")
    try:
        duration_seconds: int | None = int(float(duration))
    except (ValueError, TypeError):
        duration_seconds = None

    # Download the raw audio stream only (no -x / ffmpeg). ffmpeg.py resamples.
    out_tmpl = str(dest_dir / "%(id)s.%(ext)s")
    dl = _run_ytdlp([
        *base, "-f", "ba/best[height<=360]/18",
        "-o", out_tmpl, "--print", "after_move:filepath", url,
    ])
    audio_path = Path(dl.stdout.strip().splitlines()[-1])
    if not audio_path.is_file():
        raise DownloadError(f"yt-dlp reported {audio_path} but the file is missing")

    return DownloadResult(
        audio_path=audio_path,
        title=title,
        video_id=video_id,
        duration_seconds=duration_seconds,
    )
