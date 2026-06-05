"""Tests for scribe.pipeline.downloader URL keys and yt-dlp metadata handling."""
from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from scribe.pipeline import downloader
from scribe.pipeline.downloader import DownloadError, extract_video_id

VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\topaque-value\n"
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
        ("https://youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
        ("https://m.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
        ("https://youtu.be/jNQXAC9IVRw", "jNQXAC9IVRw"),
        ("https://www.youtube.com/shorts/abcDEF12345", "abcDEF12345"),
        ("https://www.youtube.com/embed/jNQXAC9IVRw", "jNQXAC9IVRw"),
        # extra query params, timestamps, share params — regex only cares about the v= match
        ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=42s&feature=share", "jNQXAC9IVRw"),
        # underscore + dash legal in video ids
        ("https://youtu.be/_-AbCdEf123", "_-AbCdEf123"),
    ],
)
def test_extract_video_id_valid(url: str, expected: str) -> None:
    assert extract_video_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url at all",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",  # no v= param
        "https://youtu.be/",  # no path
        # 10 chars — regex requires exactly 11
        "https://www.youtube.com/watch?v=tooShort10",
        # supported in YouTube but NOT in our regex — explicit so failure mode is obvious
        "https://www.youtube.com/v/jNQXAC9IVRw",
        "https://www.youtube.com/live/jNQXAC9IVRw",
    ],
)
def test_extract_video_id_invalid_raises(url: str) -> None:
    with pytest.raises(DownloadError):
        extract_video_id(url)


def test_initial_video_key_accepts_non_youtube_url() -> None:
    first = downloader.initial_video_key("https://x.com/i/status/2057105488165163198")
    second = downloader.initial_video_key("https://x.com/i/status/2057105488165163198")
    assert first == second
    assert first.startswith("pending:")


def test_normalized_video_key_keeps_youtube_dedup_shape() -> None:
    assert downloader.normalized_video_key("Youtube", "jNQXAC9IVRw") == "jNQXAC9IVRw"


def test_normalized_video_key_qualifies_non_youtube_provider() -> None:
    assert downloader.normalized_video_key("Twitter", "2057105488165163198") == "twitter:2057105488165163198"


def test_download_audio_uses_ytdlp_extractor_key_for_non_youtube(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")

    def fake_run(args):
        if "--dump-single-json" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "extractor_key": "Twitter",
                        "id": "2057105488165163198",
                        "title": "tweet video",
                        "duration": 12.4,
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{media}\n", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)

    result = downloader.download_audio("https://x.com/i/status/2057105488165163198", tmp_path)

    assert result.video_id == "twitter:2057105488165163198"
    assert result.title == "tweet video"
    assert result.duration_seconds == 12
    assert result.audio_path == media


def _fake_ytdlp_success(media_path):
    def run(args):
        if "--dump-single-json" in args:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "extractor_key": "Youtube",
                        "id": "jNQXAC9IVRw",
                        "title": "ok",
                        "duration": 10,
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{media_path}\n", stderr="")

    return run


def test_download_audio_without_cookies_omits_cookies_flag(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")
    seen: list[list[str]] = []

    fake = _fake_ytdlp_success(media)

    def capture(args):
        seen.append(list(args))
        return fake(args)

    monkeypatch.setattr(downloader, "_run_ytdlp", capture)

    downloader.download_audio("https://youtu.be/jNQXAC9IVRw", tmp_path)

    assert len(seen) == 2
    for args in seen:
        assert "--cookies" not in args


def test_download_audio_with_cookies_writes_0600_temp_and_passes_flag(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")
    seen_paths: list[str] = []

    fake = _fake_ytdlp_success(media)

    def capture(args):
        # Capture the cookies arg and snapshot its mode/contents *during*
        # the download — both yt-dlp calls must see the same path.
        idx = args.index("--cookies")
        cookie_path = args[idx + 1]
        seen_paths.append(cookie_path)
        st = os.stat(cookie_path)
        # 0600 — owner-only read/write, no group/other bits.
        assert stat.S_IMODE(st.st_mode) == 0o600
        with open(cookie_path, encoding="utf-8") as fh:
            assert fh.read() == VALID_COOKIES
        return fake(args)

    monkeypatch.setattr(downloader, "_run_ytdlp", capture)

    downloader.download_audio(
        "https://youtu.be/jNQXAC9IVRw", tmp_path, cookies=VALID_COOKIES
    )

    assert len(seen_paths) == 2
    # Both invocations saw the *same* temp file path.
    assert seen_paths[0] == seen_paths[1]
    # The temp is gone after download_audio returned.
    assert not os.path.exists(seen_paths[0])


def test_download_audio_with_cookies_cleans_up_on_failure(tmp_path, monkeypatch) -> None:
    seen_path: dict[str, str] = {}

    def failing(args):
        idx = args.index("--cookies")
        seen_path["p"] = args[idx + 1]
        # Sanity: the temp exists during the call.
        assert os.path.exists(seen_path["p"])
        raise downloader.DownloadError("simulated yt-dlp failure")

    monkeypatch.setattr(downloader, "_run_ytdlp", failing)

    with pytest.raises(downloader.DownloadError):
        downloader.download_audio(
            "https://youtu.be/jNQXAC9IVRw", tmp_path, cookies=VALID_COOKIES
        )

    assert "p" in seen_path
    assert not os.path.exists(seen_path["p"])
