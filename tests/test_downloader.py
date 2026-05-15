"""Tests for scribe.pipeline.downloader.extract_video_id — pure regex, no
network. Regex matches `v=`/`youtu.be/`/`/shorts/`/`/embed/` followed by
exactly 11 chars from [0-9A-Za-z_-]. Anything else raises DownloadError."""
from __future__ import annotations

import pytest

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
