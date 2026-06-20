"""Tests for scribe.pipeline.downloader URL keys and yt-dlp metadata handling."""
from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from scribe.pipeline import downloader
from scribe.pipeline.downloader import (
    REASON_BOTWALL_TRANSIENT,
    REASON_DOWNLOAD_TIMEOUT,
    REASON_NEEDS_COOKIES,
    REASON_OTHER,
    REASON_UNAVAILABLE,
    DownloadError,
    classify_ytdlp_failure,
    extract_video_id,
)

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

    def fake_run(args, **kwargs):
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
    def run(args, **kwargs):
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


def test_download_audio_without_pot_base_url_omits_bgutil_extractor_arg(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")
    seen: list[list[str]] = []

    fake = _fake_ytdlp_success(media)

    def capture(args, **kwargs):
        seen.append(list(args))
        return fake(args)

    monkeypatch.setattr(downloader, "_run_ytdlp", capture)

    downloader.download_audio("https://youtu.be/jNQXAC9IVRw", tmp_path)

    assert len(seen) == 2
    for args in seen:
        # No bgutil extractor-args at all when pot_base_url is unset.
        assert not any("youtubepot-bgutilhttp" in a for a in args)


def test_download_audio_with_pot_base_url_passes_bgutil_extractor_arg(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")
    seen: list[list[str]] = []

    fake = _fake_ytdlp_success(media)

    def capture(args, **kwargs):
        seen.append(list(args))
        return fake(args)

    monkeypatch.setattr(downloader, "_run_ytdlp", capture)

    downloader.download_audio(
        "https://youtu.be/jNQXAC9IVRw",
        tmp_path,
        pot_base_url="http://scribe-pot:4416",
    )

    assert len(seen) == 2
    expected = "youtubepot-bgutilhttp:base_url=http://scribe-pot:4416"
    for args in seen:
        # The bgutil extractor-arg appears alongside the youtube:player_client
        # one; both share the --extractor-args flag but yt-dlp accepts
        # repeating it for independent provider/extractor scopes.
        idx_flags = [i for i, a in enumerate(args) if a == "--extractor-args"]
        flag_values = [args[i + 1] for i in idx_flags]
        assert expected in flag_values
        # The pre-existing player_client arg is preserved.
        assert any(v.startswith("youtube:player_client=") for v in flag_values)


def test_download_audio_without_cookies_omits_cookies_flag(tmp_path, monkeypatch) -> None:
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")
    seen: list[list[str]] = []

    fake = _fake_ytdlp_success(media)

    def capture(args, **kwargs):
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

    def capture(args, **kwargs):
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


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        # Bot-wall — transient soft-ban that the backoff path is designed for.
        (
            "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication. "
            "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp",
            REASON_BOTWALL_TRANSIENT,
        ),
        (
            "ERROR: [youtube] abcDEFghij1: LOGIN_REQUIRED: Sign in to confirm "
            "you're not a bot.",
            REASON_BOTWALL_TRANSIENT,
        ),
        # Age/sign-in/members gating — cookies would lift this.
        (
            "ERROR: [youtube] xxxxxxxxxxx: Sign in to confirm your age. "
            "This video may be inappropriate for some users.",
            REASON_NEEDS_COOKIES,
        ),
        (
            "ERROR: [youtube] yyyyyyyyyyy: Join this channel to get access to "
            "members-only content.",
            REASON_NEEDS_COOKIES,
        ),
        # Permanently unavailable — not retryable, not unlockable with cookies.
        (
            "ERROR: [youtube] zzzzzzzzzzz: Private video. Sign in if you've "
            "been granted access to this video",
            REASON_UNAVAILABLE,
        ),
        (
            "ERROR: [youtube] qqqqqqqqqqq: Video unavailable. This video has "
            "been removed by the uploader",
            REASON_UNAVAILABLE,
        ),
        (
            "ERROR: [youtube] wwwwwwwwwww: The uploader has not made this "
            "video available in your country",
            REASON_UNAVAILABLE,
        ),
        # Everything else.
        ("ERROR: unable to download webpage: HTTP Error 500", REASON_OTHER),
        ("", REASON_OTHER),
    ],
)
def test_classify_ytdlp_failure(stderr: str, expected: str) -> None:
    assert classify_ytdlp_failure(stderr) == expected


def test_run_ytdlp_retries_botwall_with_backoff(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(args, capture_output, text):
        calls["n"] += 1
        if calls["n"] < 3:
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="Sign in to confirm you're not a bot"
            )
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    sleeps: list[float] = []
    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.time, "sleep", lambda d: sleeps.append(d))
    # Pin random so the test is deterministic.
    monkeypatch.setattr(downloader.random, "uniform", lambda a, b: 0.0)

    result = downloader._run_ytdlp(["yt-dlp", "x"])

    assert result.returncode == 0
    assert calls["n"] == 3
    # 8s then 16s with zero jitter.
    assert sleeps == [8.0, 16.0]


def test_run_ytdlp_does_not_retry_needs_cookies(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(args, capture_output, text):
        calls["n"] += 1
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Sign in to confirm your age"
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.time, "sleep", lambda d: pytest.fail("must not sleep"))

    with pytest.raises(DownloadError) as exc_info:
        downloader._run_ytdlp(["yt-dlp", "x"])

    assert exc_info.value.reason == REASON_NEEDS_COOKIES
    assert calls["n"] == 1


def test_run_ytdlp_does_not_retry_unavailable(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(args, capture_output, text):
        calls["n"] += 1
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Private video"
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.time, "sleep", lambda d: pytest.fail("must not sleep"))

    with pytest.raises(DownloadError) as exc_info:
        downloader._run_ytdlp(["yt-dlp", "x"])

    assert exc_info.value.reason == REASON_UNAVAILABLE
    assert calls["n"] == 1


def test_run_ytdlp_botwall_exhausts_retries_and_raises_typed_reason(monkeypatch) -> None:
    def fake_run(args, capture_output, text):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Sign in to confirm you're not a bot"
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.time, "sleep", lambda d: None)
    monkeypatch.setattr(downloader.random, "uniform", lambda a, b: 0.0)

    with pytest.raises(DownloadError) as exc_info:
        downloader._run_ytdlp(["yt-dlp", "x"])

    assert exc_info.value.reason == REASON_BOTWALL_TRANSIENT


def test_run_ytdlp_backoff_respects_total_cap(monkeypatch) -> None:
    """Cumulative sleep must not exceed MAX_TOTAL_BACKOFF_SECONDS."""

    def fake_run(args, capture_output, text):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Sign in to confirm you're not a bot"
        )

    sleeps: list[float] = []
    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.time, "sleep", lambda d: sleeps.append(d))
    monkeypatch.setattr(downloader.random, "uniform", lambda a, b: b)  # max jitter

    with pytest.raises(DownloadError):
        downloader._run_ytdlp(["yt-dlp", "x"])

    assert sum(sleeps) <= downloader.MAX_TOTAL_BACKOFF_SECONDS


def test_download_audio_with_cookies_cleans_up_on_failure(tmp_path, monkeypatch) -> None:
    seen_path: dict[str, str] = {}

    def failing(args, **kwargs):
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


# --- per-stage download timeout (#346) --------------------------------------
def test_invoke_ytdlp_deadline_expired_raises_download_timeout(monkeypatch, tmp_path):
    """A yt-dlp invocation that outlives the deadline raises a typed
    DownloadError(reason=download_timeout) and SIGKILLs the process group."""
    import time as _time

    deadline = _time.monotonic() + 0.05
    with pytest.raises(DownloadError) as exc_info:
        downloader._invoke_ytdlp(["sleep", "30"], deadline)
    assert exc_info.value.reason == REASON_DOWNLOAD_TIMEOUT


def test_invoke_ytdlp_kills_process_group_on_timeout():
    """The yt-dlp process group is SIGKILLed on deadline, so the helper
    returns promptly instead of waiting the full subprocess duration."""
    import time as _time

    start = _time.monotonic()
    deadline = start + 0.05
    with pytest.raises(DownloadError):
        downloader._invoke_ytdlp(["sleep", "30"], deadline)
    elapsed = _time.monotonic() - start
    # If the process group were not killed, the fallback communicate(timeout=5)
    # would push elapsed toward ~5s. Sub-3s proves the SIGKILL landed and no
    # orphaned sleep keeps the worker pinned.
    assert elapsed < 3.0


def test_run_ytdlp_timeout_does_not_retry(monkeypatch):
    """A download timeout must not be retried as if it were a transient
    bot-wall hit — the deadline is gone, so retrying would only stall."""
    from scribe.config import settings

    monkeypatch.setattr(settings, "download_timeout_s", 0.05)

    calls = {"n": 0}

    def fake_invoke(args, deadline):
        calls["n"] += 1
        # Simulate a hang that blows past the deadline.
        raise DownloadError("download timed out", reason=REASON_DOWNLOAD_TIMEOUT)

    monkeypatch.setattr(downloader, "_invoke_ytdlp", fake_invoke)
    monkeypatch.setattr(downloader.time, "sleep", lambda _d: pytest.fail("must not sleep"))

    with pytest.raises(DownloadError) as exc_info:
        downloader._run_ytdlp(["yt-dlp", "x"], deadline=_time_deadline())

    assert exc_info.value.reason == REASON_DOWNLOAD_TIMEOUT
    assert calls["n"] == 1


def _time_deadline() -> float:
    import time as _time

    return _time.monotonic() + 1.0


def test_download_audio_timeout_raises_download_timeout_and_kills_ytdlp(monkeypatch, tmp_path):
    """End-to-end: download_audio with a tight timeout_seconds and a yt-dlp
    invocation that hangs must fail with reason=download_timeout and leave no
    orphaned yt-dlp subprocess."""
    from scribe.config import settings

    monkeypatch.setattr(settings, "download_timeout_s", 600)  # ensure default is sane

    seen_args: list[list[str]] = []

    # Force the yt-dlp invocation to hang by making _base_args emit a shell
    # sleep; the trailing yt-dlp flags become positional args to `sh -c` and
    # are ignored, so the subprocess really blocks for 30s. The real deadline
    # comes from timeout_seconds below.
    monkeypatch.setattr(
        downloader,
        "_base_args",
        lambda *_a, **_kw: ["sh", "-c", "sleep 30"],
    )

    # Wrap _invoke_ytdlp so the test fails loudly if patching is wrong.
    real_invoke = downloader._invoke_ytdlp

    def spying_invoke(args, deadline):
        seen_args.append(list(args))
        return real_invoke(args, deadline)

    monkeypatch.setattr(downloader, "_invoke_ytdlp", spying_invoke)

    with pytest.raises(DownloadError) as exc_info:
        downloader.download_audio(
            "https://youtu.be/jNQXAC9IVRw",
            tmp_path,
            timeout_seconds=0.1,
        )
    assert exc_info.value.reason == REASON_DOWNLOAD_TIMEOUT
    assert seen_args, "download_audio must have invoked yt-dlp at least once"


def test_download_audio_timeout_zero_disables_timeout(monkeypatch, tmp_path):
    """timeout_seconds=0 keeps the legacy unbounded behaviour (canary/tests)."""
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "--dump-single-json" in args:
            return subprocess.CompletedProcess(
                args, 0,
                stdout=json.dumps({"extractor_key": "Youtube", "id": "jNQXAC9IVRw",
                                    "title": "ok", "duration": 10}),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{media}\n", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)

    result = downloader.download_audio(
        "https://youtu.be/jNQXAC9IVRw", tmp_path, timeout_seconds=0
    )
    assert result.video_id == "jNQXAC9IVRw"
