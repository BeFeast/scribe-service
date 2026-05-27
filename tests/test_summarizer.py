"""Tests for scribe.pipeline.summarizer — high-level summarize() entrypoint.

The legacy codex subprocess + stderr-detection unit tests now live alongside
`CodexProvider` in `tests/test_summary_providers.py`. The cases here cover the
summarize() wrapper itself: prompt assembly, tag normalisation, and the
chain-failure path that surfaces `SummarizeError` / `CodexTokenRevokedError`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scribe.pipeline import prompts, summarizer, summary_providers


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    summary_providers._reset_breakers_for_test()
    yield
    summary_providers._reset_breakers_for_test()


@pytest.fixture()
def _codex_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict the chain to codex so subprocess-mocked tests don't fall
    through to claude/freellmapi providers."""
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["codex"])


# ---------- _slugify ----------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hello World", "hello-world"),
        ("UPPER lower 123", "upper-lower-123"),
        ("  spaced  out  ", "spaced-out"),
        ("special!@#$%chars", "special-chars"),
        ("", "transcript"),
        ("---", "transcript"),
        ("---a---", "a"),
    ],
)
def test_slugify(value: str, expected: str) -> None:
    assert summarizer._slugify(value) == expected


def test_normalize_tags_replaces_transliteration_and_keeps_technical_tags() -> None:
    assert summarizer._normalize_tags(
        [
            "bytovaya-scena",
            "detskaya-rech",
            "dok-stancii",
            "internet-kultura",
            "intuiciya",
            "karera",
            "periferia",
            "plagin",
            "poisk",
            "pokupka",
            "predprinimatelstvo",
            "produktivnost",
            "smertnost",
            "stiv-dzhobs",
            "usb-huby",
            "Apple",
            "ai-economics",
            "ai-security",
            "apple-silicon",
            "backups",
            "Claude Code",
            "coding-agent",
            "tag1",
        ]
    ) == [
        "everyday-scene",
        "child-speech",
        "docking-stations",
        "internet-culture",
        "intuition",
        "career",
        "peripherals",
        "plugins",
        "search",
        "buying-guide",
        "entrepreneurship",
        "productivity",
        "mortality",
        "steve-jobs",
        "usb-hubs",
        "apple",
        "ai-economics",
        "ai-security",
        "apple-silicon",
        "backups",
        "claude-code",
        "coding-agent",
    ]


# ---------- summarize() end-to-end with codex backend mocked -----------------


def _write_prompt_dir(tmp_path: Path, body: str = "Prompt {date} {transcript_slug}") -> None:
    for version in prompts.PROMPT_VERSIONS:
        (tmp_path / f"transcript-summary.{version}.md").write_text(
            f"{body} ({version})\n\n## TL;DR\n\nx\n\n## Details\n\ny\n",
            encoding="utf-8",
        )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")


def test_summarize_reads_active_prompt_each_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _codex_only: None,
) -> None:
    """The worker path should pick up active-version changes without restart."""
    _write_prompt_dir(tmp_path, body="Prompt {date} {transcript_slug}")
    active_path = tmp_path / "transcript-summary.active"

    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")

    seen_prompts: list[str] = []

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        seen_prompts.append(input)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("---\ntags: [dry-run]\n---\n\nsummary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)

    summarizer.summarize("Transcript", title="First Title")
    active_path.write_text("v2\n", encoding="utf-8")
    summarizer.summarize("Transcript", title="Second Title")

    assert "(v1)" in seen_prompts[0]
    assert "(v2)" in seen_prompts[1]


def test_summarize_injects_short_description_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _codex_only: None,
) -> None:
    (tmp_path / "transcript-summary.v1.md").write_text(
        "Prompt {date} {transcript_slug} {short_description_language_name}",
        encoding="utf-8",
    )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")

    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")
    monkeypatch.setattr(summarizer.settings, "short_description_language", "ru")

    seen_prompts: list[str] = []

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        seen_prompts.append(input)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("---\ntags: [dry-run]\n---\n\nsummary", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)

    summarizer.summarize("Transcript", title="Language Test")

    assert "Russian" in seen_prompts[0]
    assert "{short_description_language_name}" not in seen_prompts[0]


def test_summarize_parses_short_description_from_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _codex_only: None,
) -> None:
    (tmp_path / "transcript-summary.v1.md").write_text(
        "Prompt {date} {transcript_slug}",
        encoding="utf-8",
    )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")

    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            "---\n"
            "tags: [systems, ai]\n"
            'short_description: "Короткое описание для карточки. Оно звучит завершённо."\n'
            "---\n\n"
            "# Summary\n\nFull body stays intact.",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)

    result = summarizer.summarize("Transcript", title="Parser Test")

    assert result.tags == ["systems", "ai"]
    assert result.short_description == "Короткое описание для карточки. Оно звучит завершённо."
    assert "Full body stays intact." in result.summary_md
