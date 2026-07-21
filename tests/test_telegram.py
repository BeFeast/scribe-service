"""Tests for the secure Telegram media-reference adapter (#417).

Covers the reference contract (parse/key/routing), the two resolution paths
(HTTP stream from the Bot API `file/` endpoint and direct on-disk read from a
`telegram-bot-api --local` server), the size ceiling, the failure taxonomy, and
the secret-handling invariant that the bot token never leaks into a
`DownloadResult`, a `video_id`, or an error message.
"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe.pipeline import telegram
from scribe.pipeline.downloader import initial_video_key
from scribe.pipeline.telegram import (
    REASON_EXPIRED,
    REASON_INACCESSIBLE,
    REASON_NOT_CONFIGURED,
    REASON_TOO_LARGE,
    REASON_UNSUPPORTED,
    TelegramRefError,
    is_telegram_ref,
    parse_telegram_ref,
    telegram_video_key,
)

# A distinctive token so leak assertions are unambiguous.
_TOKEN = "123456:SECRET-BOT-TOKEN-abcdefghijklmnop"
_FILE_ID = "AgADBAADq6cxG3_valid_file_id_00001"


class _FakeResp:
    """Minimal context-manager HTTP response backed by an in-memory buffer."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_urlopen(getfile_result: dict, file_bytes: bytes = b"media-bytes", *, seen: list | None = None):
    """Build a urlopen stub dispatching getFile vs file-download by URL."""

    def _open(url: str, timeout: float | None = None) -> _FakeResp:
        if seen is not None:
            seen.append(url)
        if "/getFile" in url:
            return _FakeResp(json.dumps({"ok": True, "result": getfile_result}).encode())
        return _FakeResp(file_bytes)

    return _open


def _http_error(code: int, description: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"ok": False, "description": description}).encode())
    return urllib.error.HTTPError("https://api", code, "err", {}, body)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Reference contract: parse / key / routing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("tg:" + _FILE_ID, True),
        ("TG:" + _FILE_ID, True),
        ("  tg:" + _FILE_ID, True),
        ("https://youtu.be/jNQXAC9IVRw", False),
        ("https://example.com/a.mp4", False),
        ("", False),
    ],
)
def test_is_telegram_ref(url: str, expected: bool) -> None:
    assert is_telegram_ref(url) is expected


def test_parse_telegram_ref_valid() -> None:
    assert parse_telegram_ref("tg:" + _FILE_ID) == _FILE_ID
    assert parse_telegram_ref("  tg:" + _FILE_ID + "  ") == _FILE_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/jNQXAC9IVRw",     # not a tg: ref
        "tg:",                               # empty file_id
        "tg:short",                          # below min length
        "tg:has spaces in it here padding",  # illegal char
        "tg:../../etc/passwd_padding_00001",  # path traversal shape
        "tg:https://evil.example/x_padding",  # URL smuggled as file_id
    ],
)
def test_parse_telegram_ref_rejects_malformed(url: str) -> None:
    with pytest.raises(TelegramRefError) as exc:
        parse_telegram_ref(url)
    assert exc.value.reason == REASON_UNSUPPORTED


def test_telegram_video_key_is_deterministic_and_opaque() -> None:
    key = telegram_video_key(_FILE_ID)
    assert key == telegram_video_key(_FILE_ID)
    assert key.startswith("telegram:")
    # The raw file_id never appears verbatim in the key.
    assert _FILE_ID not in key


def test_initial_video_key_routes_tg_prefix_to_telegram_key() -> None:
    # The API/submit path and the adapter must agree on the key so the worker's
    # telegram dispatch and submit-time dedup both see the same marker.
    assert initial_video_key("tg:" + _FILE_ID) == telegram_video_key(_FILE_ID)


def test_initial_video_key_preserves_youtube_and_pending() -> None:
    # Regression guard for criterion #5: existing behavior is untouched.
    assert initial_video_key("https://youtu.be/jNQXAC9IVRw") == "jNQXAC9IVRw"
    assert initial_video_key("https://example.com/a.mp4").startswith("pending:")


# --------------------------------------------------------------------------- #
# Success paths
# --------------------------------------------------------------------------- #


def test_resolve_and_download_streams_from_bot_api(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        telegram,
        "_urlopen",
        _fake_urlopen({"file_path": "videos/file_5.mp4", "file_size": 11}, b"media-bytes", seen=seen),
    )
    result = telegram.resolve_and_download(
        "tg:" + _FILE_ID, tmp_path, token=_TOKEN, api_base_url="https://api.telegram.org"
    )
    assert result.source_platform == "telegram"
    assert result.video_id == telegram_video_key(_FILE_ID)
    assert result.audio_path.exists()
    assert result.audio_path.read_bytes() == b"media-bytes"
    assert result.audio_path.suffix == ".mp4"
    # The token is used against the API (proves resolution happened)...
    assert any(_TOKEN in u for u in seen)
    # ...but never leaks into the job-facing result.
    assert _TOKEN not in repr(result)
    assert _TOKEN not in result.video_id


def test_resolve_and_download_reads_local_bot_api_file(tmp_path: Path, monkeypatch) -> None:
    # A `telegram-bot-api --local` server returns an absolute on-disk path;
    # the adapter copies it in with no HTTP transfer.
    src = tmp_path / "server_storage" / "clip.ogg"
    src.parent.mkdir()
    src.write_bytes(b"local-audio-payload")

    def _no_http(url: str, timeout: float | None = None):
        if "/getFile" in url:
            return _FakeResp(json.dumps({"ok": True, "result": {"file_path": str(src)}}).encode())
        raise AssertionError("must not perform an HTTP download for a local file path")

    monkeypatch.setattr(telegram, "_urlopen", _no_http)
    dest = tmp_path / "job"
    result = telegram.resolve_and_download("tg:" + _FILE_ID, dest, token=_TOKEN)
    assert result.audio_path.read_bytes() == b"local-audio-payload"
    # Copied into the job tmpdir, not the server's own storage.
    assert result.audio_path.parent == dest
    assert src.exists()  # source is left untouched


# --------------------------------------------------------------------------- #
# Size ceiling
# --------------------------------------------------------------------------- #


def test_rejects_oversize_file_size_before_download(tmp_path: Path, monkeypatch) -> None:
    def _open(url: str, timeout: float | None = None):
        assert "/getFile" in url, "download must not start for an oversize file"
        return _FakeResp(json.dumps({"ok": True, "result": {"file_path": "a.mp4", "file_size": 999}}).encode())

    monkeypatch.setattr(telegram, "_urlopen", _open)
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN, max_bytes=100)
    assert exc.value.reason == REASON_TOO_LARGE


def test_aborts_midstream_when_download_exceeds_cap(tmp_path: Path, monkeypatch) -> None:
    # getFile omits file_size, so only the mid-stream guard can catch it.
    monkeypatch.setattr(
        telegram,
        "_urlopen",
        _fake_urlopen({"file_path": "big.mp4"}, b"x" * (5 * 1024 * 1024)),
    )
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN, max_bytes=1024)
    assert exc.value.reason == REASON_TOO_LARGE
    # The partial file is cleaned up.
    assert not any(tmp_path.iterdir())


# --------------------------------------------------------------------------- #
# Failure taxonomy
# --------------------------------------------------------------------------- #


def test_not_configured_when_token_missing(tmp_path: Path) -> None:
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token="")
    assert exc.value.reason == REASON_NOT_CONFIGURED


def test_unsupported_reference_fails_before_any_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        telegram, "_urlopen", lambda *a, **k: pytest.fail("must not call the API")
    )
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("https://youtu.be/x", tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_UNSUPPORTED


def test_expired_reference_from_getfile_ok_false(tmp_path: Path, monkeypatch) -> None:
    def _open(url: str, timeout: float | None = None):
        return _FakeResp(json.dumps({"ok": False, "description": "wrong file_id/FILE_ID_INVALID"}).encode())

    monkeypatch.setattr(telegram, "_urlopen", _open)
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_EXPIRED


def test_getfile_http_error_is_classified(tmp_path: Path, monkeypatch) -> None:
    def _open(url: str, timeout: float | None = None):
        raise _http_error(400, "Bad Request: file is too big")

    monkeypatch.setattr(telegram, "_urlopen", _open)
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_TOO_LARGE


def test_network_failure_is_inaccessible(tmp_path: Path, monkeypatch) -> None:
    def _open(url: str, timeout: float | None = None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(telegram, "_urlopen", _open)
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_INACCESSIBLE


def test_getfile_without_file_path_is_expired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telegram, "_urlopen", _fake_urlopen({"file_size": 5}))
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_EXPIRED


def test_empty_download_body_is_expired(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(telegram, "_urlopen", _fake_urlopen({"file_path": "a.mp4"}, b""))
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    assert exc.value.reason == REASON_EXPIRED


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        ("Bad Request: file is too big", REASON_TOO_LARGE),
        ("Bad Request: wrong file_id", REASON_EXPIRED),
        ("Bad Request: invalid file_id", REASON_EXPIRED),
        ("Bad Request: file not found", REASON_EXPIRED),
        ("Bad Request: wrong remote file identifier specified", REASON_EXPIRED),
        ("Internal Server Error", REASON_INACCESSIBLE),
        ("", REASON_INACCESSIBLE),
    ],
)
def test_classify_api_error(description: str, reason: str) -> None:
    assert telegram._classify_api_error(description) == reason


# --------------------------------------------------------------------------- #
# Secret handling
# --------------------------------------------------------------------------- #


def test_error_messages_never_contain_the_token(tmp_path: Path, monkeypatch) -> None:
    def _open(url: str, timeout: float | None = None):
        raise _http_error(400, "Bad Request: wrong file_id")

    monkeypatch.setattr(telegram, "_urlopen", _open)
    with pytest.raises(TelegramRefError) as exc:
        telegram.resolve_and_download("tg:" + _FILE_ID, tmp_path, token=_TOKEN)
    message = str(exc.value)
    assert _TOKEN not in message
    assert _FILE_ID not in message  # raw reference is not echoed either


def test_token_registered_as_redacted_secret_field() -> None:
    # The token setting must be scrubbed from every log line (criterion #3).
    from scribe.obs.logging import _SECRET_SETTING_FIELDS

    assert "telegram_bot_token" in _SECRET_SETTING_FIELDS


# --------------------------------------------------------------------------- #
# Worker routing
# --------------------------------------------------------------------------- #


def test_worker_routes_telegram_video_id_prefix() -> None:
    from scribe.worker.loop import _is_telegram_job, _is_upload_job

    tg_job = SimpleNamespace(video_id=telegram_video_key(_FILE_ID))
    yt_job = SimpleNamespace(video_id="jNQXAC9IVRw")
    upload_job = SimpleNamespace(video_id="upload:abc123")

    assert _is_telegram_job(tg_job) is True
    assert _is_telegram_job(yt_job) is False
    assert _is_telegram_job(upload_job) is False
    # Mutually exclusive with the upload path.
    assert _is_upload_job(tg_job) is False
