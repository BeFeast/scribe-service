"""Secure Telegram media-reference adapter (#417).

A Telegram user can send media larger than the consumer bot's inline download
path supports. In that case the integration only holds an *opaque* Telegram
media reference — a ``file_id`` — not a URL Scribe can hand to yt-dlp. This
module is the one component allowed to resolve that reference into bytes.

Contract (see ``docs/telegram-media-ingestion.md``):

* The integration submits ``tg:<file_id>`` as the ``url`` of a normal
  ``POST /jobs`` request. The reference is opaque and carries **no** secret:
  no bot token, no session string, no signed URL.
* Resolution uses a bot token held only in server config
  (``settings.telegram_bot_token``). The token is scrubbed from every log line
  (:mod:`scribe.obs.logging`) and never appears in a job record, API payload,
  or the messages of the errors this module raises.
* Resolution goes through the Telegram Bot API: ``getFile`` returns a
  ``file_path``; the bytes are then read from that path. Against the public
  ``api.telegram.org`` the download ceiling is 20 MB; pointing
  ``settings.telegram_api_base_url`` at a self-hosted ``telegram-bot-api``
  server lifts it to 2 GB and, in ``--local`` mode, returns an on-disk path the
  adapter reads directly with no HTTP transfer.
* ``file_id`` references are not permanent. An expired/invalid/inaccessible
  reference surfaces as a typed, user-facing :class:`TelegramRefError`.

The result mirrors :class:`scribe.pipeline.downloader.DownloadResult` so the
rest of the worker pipeline (ffmpeg → whisper → summary) is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from scribe.config import settings
from scribe.pipeline.downloader import DownloadResult

TELEGRAM_SCHEME = "tg:"

# Telegram file_ids are URL-safe base64-ish blobs: letters, digits, ``-``/``_``.
# Anchored + bounded so an accidental URL or path cannot masquerade as a ref.
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,512}$")

# Public reason taxonomy. Stable strings the API/worker error surface branches
# on, mirroring downloader.REASON_* so callers can treat both uniformly.
REASON_UNSUPPORTED = "telegram_unsupported"      # not a well-formed tg: ref
REASON_NOT_CONFIGURED = "telegram_not_configured"  # no bot token in server config
REASON_EXPIRED = "telegram_expired"              # file_id expired / invalid / gone
REASON_TOO_LARGE = "telegram_too_large"          # exceeds the download ceiling
REASON_INACCESSIBLE = "telegram_inaccessible"    # network / API failure
REASON_OTHER = "telegram_other"

_CHUNK = 1024 * 1024

# Indirection so tests can substitute the HTTP layer without real network I/O.
# Everything token-bearing flows through here; nothing in this module logs the
# request URL, so the token never reaches a log sink through this path.
_urlopen = urllib.request.urlopen


class TelegramRefError(RuntimeError):
    """Raised when a Telegram media reference cannot be resolved to bytes.

    ``reason`` is one of the module-level ``REASON_*`` strings. The message is
    always user-facing and secret-free (never contains the bot token, the
    resolved download URL, or the raw ``file_id``)."""

    def __init__(self, message: str, *, reason: str = REASON_OTHER) -> None:
        super().__init__(message)
        self.reason = reason


def is_telegram_ref(url: str) -> bool:
    """True when ``url`` uses the ``tg:`` media-reference scheme."""
    return bool(url) and url.strip().lower().startswith(TELEGRAM_SCHEME)


def parse_telegram_ref(url: str) -> str:
    """Extract and validate the ``file_id`` from a ``tg:<file_id>`` reference.

    Raises :class:`TelegramRefError` (``reason=telegram_unsupported``) when the
    value is not a well-formed reference. The error message never echoes the
    offending value beyond its shape, so a malformed submission can't smuggle
    content into a user-facing string."""
    if not is_telegram_ref(url):
        raise TelegramRefError(
            "not a Telegram media reference (expected 'tg:<file_id>')",
            reason=REASON_UNSUPPORTED,
        )
    file_id = url.strip()[len(TELEGRAM_SCHEME):].strip()
    if not _FILE_ID_RE.match(file_id):
        raise TelegramRefError(
            "malformed Telegram media reference: expected 'tg:<file_id>' with "
            "a URL-safe file id",
            reason=REASON_UNSUPPORTED,
        )
    return file_id


def telegram_video_key(file_id: str) -> str:
    """Stable dedup key for a Telegram reference.

    Telegram ``file_id`` values are long and bot-scoped; we key on a short
    digest so the value never lands verbatim in a ``video_id`` column, log, or
    URL. Same reference in → same key, so submit-time dedup works like YouTube."""
    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:24]
    return f"telegram:{digest}"


def _classify_api_error(description: str) -> str:
    """Map a Telegram Bot API error ``description`` to a ``REASON_*``."""
    text = (description or "").lower()
    if "too big" in text or "too large" in text:
        return REASON_TOO_LARGE
    # Expired / invalid / unknown reference. Telegram phrases this as
    # "wrong file_id", "wrong remote file identifier", "file not found", etc.
    if (
        "wrong" in text
        or "invalid" in text
        or "not found" in text
        or "identifier" in text
        or "file is empty" in text
    ):
        return REASON_EXPIRED
    return REASON_INACCESSIBLE


def _api_get_file(base: str, token: str, file_id: str, timeout: float) -> dict:
    """Call Bot API ``getFile`` and return the ``result`` object.

    The request URL embeds the bot token but is never logged. On any failure a
    secret-free :class:`TelegramRefError` is raised."""
    query = urllib.parse.urlencode({"file_id": file_id})
    api_url = f"{base}/bot{token}/getFile?{query}"
    try:
        with _urlopen(api_url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # getFile returns 400/404 with a JSON body describing the failure.
        description = ""
        try:
            description = json.loads(exc.read().decode("utf-8")).get("description", "")
        except Exception:
            description = ""
        reason = _classify_api_error(description) if description else REASON_INACCESSIBLE
        raise TelegramRefError(
            _user_message(reason), reason=reason
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TelegramRefError(
            _user_message(REASON_INACCESSIBLE), reason=REASON_INACCESSIBLE
        ) from None

    if not payload.get("ok"):
        reason = _classify_api_error(str(payload.get("description", "")))
        raise TelegramRefError(_user_message(reason), reason=reason)
    result = payload.get("result") or {}
    if not isinstance(result, dict) or not result.get("file_path"):
        raise TelegramRefError(
            _user_message(REASON_EXPIRED), reason=REASON_EXPIRED
        )
    return result


def _user_message(reason: str) -> str:
    """Actionable, secret-free message for each failure reason."""
    return {
        REASON_UNSUPPORTED: (
            "Unsupported Telegram media reference. Expected 'tg:<file_id>'."
        ),
        REASON_NOT_CONFIGURED: (
            "Telegram media ingestion is not configured on this server."
        ),
        REASON_EXPIRED: (
            "This Telegram media reference has expired or is no longer "
            "accessible. Please re-send the media and try again."
        ),
        REASON_TOO_LARGE: (
            "This Telegram media exceeds the supported download size. A "
            "self-hosted Bot API server is required for large files."
        ),
        REASON_INACCESSIBLE: (
            "Could not reach Telegram to resolve the media reference. "
            "Please try again shortly."
        ),
    }.get(reason, "Could not resolve the Telegram media reference.")


def _download_to(
    base: str,
    token: str,
    file_path: str,
    dest_dir: Path,
    *,
    file_id: str,
    timeout: float,
    max_bytes: int,
) -> Path:
    """Materialize the resolved media into ``dest_dir`` and return its path.

    When ``file_path`` is absolute (a ``telegram-bot-api --local`` server) the
    on-disk file is used directly with no HTTP transfer. Otherwise the bytes are
    streamed from ``{base}/file/bot{token}/{file_path}`` with the token-bearing
    URL kept internal. ``max_bytes`` is enforced both up front (when the local
    file size is known) and mid-stream so an oversize transfer is aborted."""
    suffix = Path(file_path).suffix or ".bin"
    out = dest_dir / f"telegram_{hashlib.sha256(file_id.encode()).hexdigest()[:16]}{suffix}"

    if os.path.isabs(file_path):
        src = Path(file_path)
        if not src.is_file():
            raise TelegramRefError(
                _user_message(REASON_EXPIRED), reason=REASON_EXPIRED
            )
        if src.stat().st_size > max_bytes:
            raise TelegramRefError(
                _user_message(REASON_TOO_LARGE), reason=REASON_TOO_LARGE
            )
        # Copy into the job tmpdir so the worker's cleanup owns the lifecycle
        # and never mutates/deletes the Bot API server's own storage.
        shutil.copyfile(src, out)
        return out

    file_url = f"{base}/file/bot{token}/{urllib.parse.quote(file_path)}"
    written = 0
    try:
        with _urlopen(file_url, timeout=timeout) as resp, out.open("wb") as fh:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise TelegramRefError(
                        _user_message(REASON_TOO_LARGE), reason=REASON_TOO_LARGE
                    )
                fh.write(chunk)
    except TelegramRefError:
        out.unlink(missing_ok=True)
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        out.unlink(missing_ok=True)
        raise TelegramRefError(
            _user_message(REASON_INACCESSIBLE), reason=REASON_INACCESSIBLE
        ) from None
    if written == 0:
        out.unlink(missing_ok=True)
        raise TelegramRefError(_user_message(REASON_EXPIRED), reason=REASON_EXPIRED)
    return out


def resolve_and_download(
    url: str,
    dest_dir: Path,
    *,
    token: str | None = None,
    api_base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_bytes: int | None = None,
) -> DownloadResult:
    """Resolve a ``tg:<file_id>`` reference and download its media.

    Returns a :class:`~scribe.pipeline.downloader.DownloadResult` so the media
    flows through the unchanged ffmpeg/whisper/summary pipeline. Raises
    :class:`TelegramRefError` with a typed ``reason`` and a secret-free,
    user-facing message on every failure (unconfigured token, unsupported ref,
    expired/inaccessible reference, oversize media)."""
    file_id = parse_telegram_ref(url)

    tok = (token if token is not None else settings.telegram_bot_token).strip()
    if not tok:
        raise TelegramRefError(
            _user_message(REASON_NOT_CONFIGURED), reason=REASON_NOT_CONFIGURED
        )
    base = (api_base_url if api_base_url is not None else settings.telegram_api_base_url).rstrip("/")
    timeout = timeout_seconds if timeout_seconds is not None else float(settings.telegram_download_timeout_s)
    cap = max_bytes if max_bytes is not None else settings.telegram_max_bytes

    dest_dir.mkdir(parents=True, exist_ok=True)
    info = _api_get_file(base, tok, file_id, timeout)

    file_size = info.get("file_size")
    if isinstance(file_size, int) and file_size > cap:
        raise TelegramRefError(_user_message(REASON_TOO_LARGE), reason=REASON_TOO_LARGE)

    audio_path = _download_to(
        base, tok, str(info["file_path"]), dest_dir,
        file_id=file_id, timeout=timeout, max_bytes=cap,
    )
    return DownloadResult(
        audio_path=audio_path,
        title="Telegram media",
        video_id=telegram_video_key(file_id),
        duration_seconds=None,
        source_platform="telegram",
    )
