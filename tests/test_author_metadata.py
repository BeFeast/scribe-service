"""Tests for scr-269 author/platform metadata: downloader parsing and frontmatter injector."""
from __future__ import annotations

import json
import subprocess

from scribe.pipeline import downloader
from scribe.pipeline.frontmatter_inject import inject_author_frontmatter

# ---------------- downloader: _author_from_info -----------------------------


def test_author_from_info_youtube_handle():
    name, handle, url, platform = downloader._author_from_info({
        "extractor_key": "Youtube",
        "uploader": "Marques Brownlee",
        "uploader_id": "@mkbhd",
        "uploader_url": "https://www.youtube.com/@mkbhd",
        "channel": "Marques Brownlee",
        "channel_url": "https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ",
        "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
    })
    assert name == "Marques Brownlee"
    assert handle == "@mkbhd"
    # Prefer channel_url over uploader_url when both exist.
    assert url == "https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ"
    assert platform == "youtube"


def test_author_from_info_youtube_uc_id_kept_raw():
    """A bare UCxxxxx channel id must NOT get an `@` prefix."""
    _, handle, _, _ = downloader._author_from_info({
        "extractor_key": "Youtube",
        "uploader_id": "UCBJycsmduvYEL83R_U4JriQ",
    })
    assert handle == "UCBJycsmduvYEL83R_U4JriQ"


def test_author_from_info_twitter():
    name, handle, url, platform = downloader._author_from_info({
        "extractor_key": "Twitter",
        "uploader": "Andrej Karpathy",
        "uploader_id": "karpathy",
        "uploader_url": "https://twitter.com/karpathy",
    })
    assert name == "Andrej Karpathy"
    # Non-YouTube: handle is preserved as yt-dlp returned it.
    assert handle == "karpathy"
    assert url == "https://twitter.com/karpathy"
    assert platform == "twitter"


def test_author_from_info_instagram():
    _, _, _, platform = downloader._author_from_info({"extractor_key": "Instagram"})
    assert platform == "instagram"


def test_author_from_info_tiktok():
    _, _, _, platform = downloader._author_from_info({"extractor_key": "TikTok"})
    assert platform == "tiktok"


def test_author_from_info_unknown_extractor_lowercased():
    _, _, _, platform = downloader._author_from_info({"extractor_key": "WeirdSite"})
    assert platform == "weirdsite"


def test_author_from_info_empty_returns_all_none():
    assert downloader._author_from_info({}) == (None, None, None, None)


def test_download_audio_populates_author_fields(tmp_path, monkeypatch):
    media = tmp_path / "audio.m4a"
    media.write_text("audio", encoding="utf-8")

    def fake_run(args):
        if "--dump-single-json" in args:
            return subprocess.CompletedProcess(
                args, 0,
                stdout=json.dumps({
                    "extractor_key": "Youtube",
                    "id": "abcDEF12345",
                    "title": "Tech Review",
                    "duration": 600,
                    "uploader": "Marques Brownlee",
                    "uploader_id": "@mkbhd",
                    "channel_url": "https://www.youtube.com/@mkbhd",
                }),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout=f"{media}\n", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    result = downloader.download_audio("https://youtu.be/abcDEF12345", tmp_path)

    assert result.author_name == "Marques Brownlee"
    assert result.author_handle == "@mkbhd"
    assert result.author_url == "https://www.youtube.com/@mkbhd"
    assert result.source_platform == "youtube"


# ---------------- inject_author_frontmatter ---------------------------------


def test_inject_into_existing_frontmatter():
    md = '---\ntags: [foo, bar]\nshort_description: "x"\n---\n\nBody.\n'
    out = inject_author_frontmatter(
        md,
        author_name="MKBHD",
        author_handle="@mkbhd",
        author_url="https://youtube.com/@mkbhd",
        source_platform="youtube",
    )
    assert out.startswith("---\n")
    assert 'author: "MKBHD"' in out
    assert 'author_handle: "@mkbhd"' in out
    assert 'author_url: "https://youtube.com/@mkbhd"' in out
    assert 'platform: "youtube"' in out
    # Existing keys preserved
    assert "tags: [foo, bar]" in out
    assert 'short_description: "x"' in out
    # Body untouched
    assert out.endswith("Body.\n")


def test_inject_replaces_existing_author_keys():
    """Re-summarize: latest author values must overwrite stale ones."""
    md = '---\ntags: [a]\nauthor: "OLD"\nplatform: "old"\n---\n\nBody.\n'
    out = inject_author_frontmatter(md, author_name="NEW", source_platform="youtube")
    assert 'author: "NEW"' in out
    assert 'author: "OLD"' not in out
    assert 'platform: "youtube"' in out
    assert 'platform: "old"' not in out
    assert "tags: [a]" in out


def test_inject_pass_through_when_all_fields_empty():
    md = '---\ntags: [a]\n---\n\nBody.\n'
    assert inject_author_frontmatter(md) is md


def test_inject_creates_frontmatter_when_absent():
    out = inject_author_frontmatter("Plain body.\n", author_name="X", source_platform="youtube")
    assert out.startswith("---\n")
    assert 'author: "X"' in out
    assert 'platform: "youtube"' in out
    assert "Plain body." in out


def test_inject_quotes_special_chars():
    out = inject_author_frontmatter(
        "---\ntags: [a]\n---\nBody.",
        author_name='Joe "Quoted" Smith',
    )
    # Escaped: \" survives the YAML scalar
    assert r'author: "Joe \"Quoted\" Smith"' in out


def test_inject_skips_only_unset_fields():
    md = '---\ntags: [a]\n---\n\nBody.\n'
    out = inject_author_frontmatter(md, author_name="MKBHD")
    assert 'author: "MKBHD"' in out
    assert "author_handle:" not in out
    assert "author_url:" not in out
    assert "platform:" not in out


def test_inject_idempotent_on_repeated_call():
    md = '---\ntags: [a]\n---\n\nBody.\n'
    once = inject_author_frontmatter(md, author_name="MKBHD", source_platform="youtube")
    twice = inject_author_frontmatter(once, author_name="MKBHD", source_platform="youtube")
    assert once == twice


def test_inject_malformed_frontmatter_no_close_returns_unchanged():
    """Unclosed `---` block is left to the validator's repair pass."""
    md = "---\ntags: [a]\nno closing fence here\n"
    assert inject_author_frontmatter(md, author_name="X") == md
