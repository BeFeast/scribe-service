"""Tests for scribe.pipeline.downloader URL keys and yt-dlp metadata handling."""
from __future__ import annotations

import json
import subprocess

import pytest

from scribe.pipeline import downloader
from scribe.pipeline.downloader import DownloadError, extract_video_id


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
