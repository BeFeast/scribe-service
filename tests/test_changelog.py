from __future__ import annotations

from pathlib import Path

from scribe.release.changelog import (
    CHANGELOG_HEADER,
    ChangelogEntry,
    insert_release,
    parse_pr_titles,
    render_release_section,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_parse_pr_titles_extracts_numbers_dedupes_and_skips_blanks() -> None:
    log = "\n".join(
        [
            "feat(api): add endpoint (#42)",
            "",
            "fix(spa): correct layout (#43)",
            "feat(api): add endpoint (#42)",  # duplicate, dropped
            "chore: no pr number here",
        ]
    )
    entries = parse_pr_titles(log)
    assert [e.pr_number for e in entries] == [42, 43, None]
    assert [e.title for e in entries][2] == "chore: no pr number here"


def test_render_release_section_is_dated_and_lists_changes() -> None:
    section = render_release_section(
        "1.2.3",
        "2026-06-05",
        [ChangelogEntry("feat: thing", 7), ChangelogEntry("fix: other", None)],
        bump="patch",
    )
    assert "## [v1.2.3] - 2026-06-05" in section
    assert "_Bump: patch_" in section
    assert "- feat: thing (#7)" in section
    assert "- fix: other" in section


def test_render_handles_empty_changes() -> None:
    section = render_release_section("v9.9.9", "2026-01-01", [])
    assert "## [v9.9.9] - 2026-01-01" in section
    assert "- No changes recorded." in section


def test_entry_does_not_duplicate_inline_pr_number() -> None:
    entry = ChangelogEntry("feat: thing (#7)", 7)
    assert entry.render() == "- feat: thing (#7)"


def test_insert_release_prepends_newest_first() -> None:
    base = insert_release(
        CHANGELOG_HEADER,
        "v1.0.0",
        render_release_section("v1.0.0", "2026-01-01", [ChangelogEntry("initial", 1)]),
    )
    updated = insert_release(
        base,
        "v1.1.0",
        render_release_section("v1.1.0", "2026-02-02", [ChangelogEntry("next", 2)]),
    )
    assert updated.index("## [v1.1.0]") < updated.index("## [v1.0.0]")
    assert updated.startswith("# Changelog")
    assert updated.endswith("\n")


def test_insert_release_is_idempotent_for_same_version() -> None:
    section = render_release_section("v1.0.0", "2026-01-01", [ChangelogEntry("initial", 1)])
    once = insert_release(CHANGELOG_HEADER, "v1.0.0", section)
    twice = insert_release(once, "v1.0.0", section)
    assert once == twice
    assert twice.count("## [v1.0.0]") == 1


def test_repo_changelog_exists_with_dated_section() -> None:
    content = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert content.startswith("# Changelog")
    # Acceptance: a release adds a dated vX.Y.Z section.
    import re

    assert re.search(r"^## \[v\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}", content, re.MULTILINE)


def test_readme_links_to_changelog() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CHANGELOG.md" in readme
