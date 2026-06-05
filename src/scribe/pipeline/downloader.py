"""yt-dlp downloader — runs locally on the devbox.

Ported from run_vast_video_summary.py's remote_shell_script. The "Sign in to
confirm you're not a bot" wall is an IP-reputation soft-ban. Datacenter IPs
get hit hardest, but the devbox's residential IP is no longer immune — it
gets transiently flagged in short windows and then recovers. We mitigate at
three layers: (1) here, by classifying the failure and retrying bot-wall hits
with jittered exponential backoff so we don't hammer inside the same flag
window; (2) at the job level (#312), by accepting a per-job YouTube cookie
blob that lifts age/sign-in/members gates; (3) via an optional bgutil
PO-token provider (#309) — when configured, the provider base URL is
forwarded as ``--extractor-args "youtubepot-bgutilhttp:base_url=…"`` so
yt-dlp can keep mweb/web in the client chain (without a GVS PO token it logs
a warning and falls back to clients more likely to trip bot checks). The
token-free ``android_vr`` client remains the workhorse; web clients use EJS +
deno for JS challenges.

Failures are surfaced via :class:`DownloadError` with a typed ``reason``
field so callers (worker, mobile UI #306) can branch on retryable vs
needs-cookies vs permanently-unavailable.

This module only *downloads* the raw audio stream — no ``-x``/ffmpeg
postprocessing. Resampling to 16 kHz mono wav is a separate single ffmpeg
pass (see ffmpeg.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# yt-dlp tries these clients in order. android_vr is the token-free workhorse;
# the web clients use EJS + deno for JS-challenge solving.
PLAYER_CLIENTS = "mweb,web_safari,android_vr,web_embedded"
# Bot-wall (transient IP-reputation soft-ban). Tight enough to not collide
# with the age-gate "Sign in to confirm your age" string.
BOTWALL_RE = re.compile(
    r"not a bot|LOGIN_REQUIRED|sign in to confirm you(?:'|’)?re",
    re.IGNORECASE,
)
# Cookie-gated states: explicit sign-in / age-gate / members-only signals.
NEEDS_COOKIES_RE = re.compile(
    r"age[- ]restricted"
    r"|confirm your age"
    r"|inappropriate for some users"
    r"|members[- ]only"
    r"|join this channel"
    r"|sign in to view",
    re.IGNORECASE,
)
# Terminal-unavailable states: not retryable, not unlockable with cookies.
UNAVAILABLE_RE = re.compile(
    r"private video"
    r"|video unavailable"
    r"|has been removed"
    r"|removed by the uploader"
    r"|account.*terminated"
    r"|available in your country"
    r"|geo[- ]restricted"
    r"|copyright",
    re.IGNORECASE,
)

# Retry budget for the bot-wall path. Exponential 8s → 16s → 32s → 64s with
# ±25% jitter, capped at MAX_TOTAL_BACKOFF_SECONDS of cumulative sleep so a
# stuck flag-window doesn't pin a worker indefinitely.
MAX_TRIES = 4
BASE_BACKOFF_SECONDS = 8.0
MAX_BACKOFF_SECONDS = 90.0
MAX_TOTAL_BACKOFF_SECONDS = 180.0
BACKOFF_JITTER = 0.25

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})")
_NON_KEY_CHARS_RE = re.compile(r"[^a-z0-9_.-]+")


# Public reason taxonomy. Stable strings — the mobile UI (#306) branches on these.
REASON_BOTWALL_TRANSIENT = "botwall_transient"
REASON_NEEDS_COOKIES = "needs_cookies"
REASON_UNAVAILABLE = "unavailable"
REASON_OTHER = "other"


class DownloadError(RuntimeError):
    """Raised when the download stage cannot produce audio.

    ``reason`` is one of ``botwall_transient`` / ``needs_cookies`` /
    ``unavailable`` / ``other`` and is the contract the mobile error
    surface (#306) branches on.
    """

    def __init__(self, message: str, *, reason: str = REASON_OTHER) -> None:
        super().__init__(message)
        self.reason = reason


def classify_ytdlp_failure(stderr: str) -> str:
    """Map yt-dlp stderr to a stable ``DownloadError.reason``.

    Checked in priority order: ``unavailable`` first (terminal states that
    must not be retried), then ``needs_cookies`` (gated content where a
    cookie blob would lift the gate), then ``botwall_transient`` (the
    soft-ban that backoff is designed for), else ``other``.
    """
    text = stderr or ""
    if UNAVAILABLE_RE.search(text):
        return REASON_UNAVAILABLE
    if NEEDS_COOKIES_RE.search(text):
        return REASON_NEEDS_COOKIES
    if BOTWALL_RE.search(text):
        return REASON_BOTWALL_TRANSIENT
    return REASON_OTHER


def _author_from_info(info: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract (author_name, author_handle, author_url, source_platform) from
    a yt-dlp `--dump-single-json` info dict. Best-effort, no exceptions —
    every field is optional and falls back to None.

    yt-dlp normalises across extractors: `uploader` is the human-readable
    channel name, `uploader_id` is the @-handle / numeric id, `uploader_url`
    is the canonical profile URL, `channel`/`channel_url` are YouTube-only
    overrides. We prefer channel over uploader on YouTube, then uploader.
    """
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    # Map extractor_key (Youtube/Twitter/Instagram/TikTok/...) to a stable platform slug.
    platform: str | None
    if not extractor:
        platform = None
    elif extractor.startswith("youtube"):
        platform = "youtube"
    elif extractor.startswith("twitter") or extractor == "x":
        platform = "twitter"
    elif extractor.startswith("instagram"):
        platform = "instagram"
    elif extractor.startswith("tiktok"):
        platform = "tiktok"
    elif extractor.startswith("vimeo"):
        platform = "vimeo"
    else:
        platform = extractor

    name = info.get("channel") or info.get("uploader") or info.get("creator") or None
    raw_handle = info.get("uploader_id") or info.get("channel_id") or None
    handle: str | None = None
    if raw_handle:
        h = str(raw_handle).strip()
        # YouTube custom URLs already start with @; numeric channel ids stay raw.
        if h:
            handle = h if h.startswith("@") or platform != "youtube" else f"@{h}" if not h.startswith("UC") else h
    url = info.get("channel_url") or info.get("uploader_url") or None
    return (
        str(name).strip() if name else None,
        handle,
        str(url).strip() if url else None,
        platform,
    )


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    video_id: str
    duration_seconds: int | None
    author_name: str | None = None
    author_handle: str | None = None
    author_url: str | None = None
    source_platform: str | None = None


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


def _base_args(deno_path: str, pot_base_url: str | None = None) -> list[str]:
    args = [
        "yt-dlp",
        "--no-playlist",
        "--remote-components", "ejs:github",
        "--js-runtimes", f"deno:{deno_path}",
        "--extractor-args", f"youtube:player_client={PLAYER_CLIENTS}",
        "--sleep-requests", "1",
        "--min-sleep-interval", "1",
        "--max-sleep-interval", "3",
    ]
    if pot_base_url:
        # The bgutil-ytdlp-pot-provider plugin (#309) reads its provider
        # endpoint from this extractor arg; the plugin auto-loads from the
        # site-packages install in pyproject.toml. Multiple --extractor-args
        # are merged client-side by yt-dlp, so this is independent of the
        # youtube:player_client setting above.
        args += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={pot_base_url}"]
    return args


def _backoff_delay(attempt: int) -> float:
    """Jittered exponential backoff for attempt N (1-indexed).

    8s, 16s, 32s, ... clamped to MAX_BACKOFF_SECONDS, with ±BACKOFF_JITTER
    multiplicative jitter so retries from concurrent workers desynchronise.
    """
    base = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    jitter = 1.0 + random.uniform(-BACKOFF_JITTER, BACKOFF_JITTER)
    return max(0.0, base * jitter)


def _run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp; retry on the YouTube bot-wall signature with jittered
    exponential backoff, capped by ``MAX_TOTAL_BACKOFF_SECONDS`` of
    cumulative sleep. Non-bot-wall failures bail immediately and surface
    a typed ``DownloadError.reason``.
    """
    last: subprocess.CompletedProcess | None = None
    total_slept = 0.0
    for attempt in range(1, MAX_TRIES + 1):
        last = subprocess.run(args, capture_output=True, text=True)
        if last.returncode == 0:
            return last
        stderr = last.stderr or ""
        reason = classify_ytdlp_failure(stderr)
        if reason != REASON_BOTWALL_TRANSIENT or attempt >= MAX_TRIES:
            break
        delay = _backoff_delay(attempt)
        if total_slept + delay > MAX_TOTAL_BACKOFF_SECONDS:
            break
        time.sleep(delay)
        total_slept += delay
    assert last is not None
    detail = (last.stderr or last.stdout or "")[-2000:]
    reason = classify_ytdlp_failure(last.stderr or "")
    raise DownloadError(
        f"video extraction failed (yt-dlp rc={last.returncode}, reason={reason}):\n{detail}",
        reason=reason,
    )


@contextmanager
def _cookies_tempfile(blob: str | None):
    """Materialize the per-job cookie blob into a 0600 temp file for the
    duration of the download stage, then delete it (success or failure).

    Yields ``None`` when no blob is supplied so the caller can use the
    same control flow for the public-only path. The temp path is never
    logged and its contents are never read back by Python — the file
    exists only so yt-dlp can read it via ``--cookies``.
    """
    if blob is None:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="scribe-cookies-", suffix=".txt")
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            os.close(fd)
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def download_audio(
    url: str,
    dest_dir: Path,
    *,
    deno_path: str = "deno",
    cookies: str | None = None,
    pot_base_url: str | None = None,
) -> DownloadResult:
    """Download the audio stream of `url` into `dest_dir`, return metadata + path.

    When ``cookies`` is provided, the blob is written to a 0600 temp
    file and passed to both yt-dlp invocations via ``--cookies``. The
    temp is deleted on the way out of this function whether the
    download succeeded or raised.

    ``pot_base_url`` is forwarded to the bgutil-ytdlp-pot-provider plugin
    (#309) as ``--extractor-args "youtubepot-bgutilhttp:base_url=…"``. When
    empty/``None`` the integration is silently disabled and yt-dlp behaves
    as it did before the plugin was installed.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = _base_args(deno_path, pot_base_url=pot_base_url)

    with _cookies_tempfile(cookies) as cookies_path:
        cookie_args = ["--cookies", cookies_path] if cookies_path is not None else []

        meta = _run_ytdlp([*base, *cookie_args, "--skip-download", "--dump-single-json", url])
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
            *base, *cookie_args, "-f", "ba/best[height<=360]/18",
            "-o", out_tmpl, "--print", "after_move:filepath", url,
        ])
        audio_path = Path(dl.stdout.strip().splitlines()[-1])
        if not audio_path.is_file():
            raise DownloadError(f"yt-dlp reported {audio_path} but the file is missing")

        author_name, author_handle, author_url, source_platform = _author_from_info(info)
        return DownloadResult(
            audio_path=audio_path,
            title=title,
            video_id=video_id,
            duration_seconds=duration_seconds,
            author_name=author_name,
            author_handle=author_handle,
            author_url=author_url,
            source_platform=source_platform,
        )
