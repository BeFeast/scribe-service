"""Pure helpers for building and updating ``CHANGELOG.md`` per release.

The release flow collects the merged pull-request titles in a tag range, renders
a dated ``vX.Y.Z`` section, and inserts it at the top of ``CHANGELOG.md`` (newest
first). Everything here is side-effect free so it can be unit-tested; the thin
CLI in ``scripts/generate_changelog.py`` wires it to ``git`` and the filesystem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CHANGELOG_HEADER = (
    "# Changelog\n\n"
    "All notable changes to this project are documented here. Each `vX.Y.Z`\n"
    "section is generated per release from the merged pull-request titles in the\n"
    "tag range. See the README for how a release is cut.\n"
)

# A first-parent merge of a GitHub PR keeps the PR number in the subject, e.g.
# "feat(downloader): jittered backoff (#310)". Squash-merges land the same way.
_PR_NUMBER_RE = re.compile(r"\(#(\d+)\)\s*$")
_VERSION_HEADING_RE = re.compile(r"^## \[(?P<version>v[^\]]+)\]", re.MULTILINE)


@dataclass(frozen=True)
class ChangelogEntry:
    """One line item in a release section."""

    title: str
    pr_number: int | None = None

    def render(self) -> str:
        if self.pr_number is not None and _PR_NUMBER_RE.search(self.title) is None:
            return f"- {self.title} (#{self.pr_number})"
        return f"- {self.title}"


def parse_pr_titles(git_log: str) -> list[ChangelogEntry]:
    """Turn raw ``git log --pretty=%s`` output into changelog entries.

    Empty lines are dropped and exact-duplicate subjects are collapsed while
    preserving first-seen order, so a squash-merge and its follow-up revert do
    not both clutter the section.
    """
    entries: list[ChangelogEntry] = []
    seen: set[str] = set()
    for raw in git_log.splitlines():
        title = raw.strip()
        if not title or title in seen:
            continue
        seen.add(title)
        match = _PR_NUMBER_RE.search(title)
        pr_number = int(match.group(1)) if match else None
        entries.append(ChangelogEntry(title=title, pr_number=pr_number))
    return entries


def render_release_section(
    version: str,
    date: str,
    entries: list[ChangelogEntry],
    bump: str | None = None,
) -> str:
    """Render a single dated ``vX.Y.Z`` section.

    ``version`` is normalised to a leading ``v``. ``date`` is an ISO ``YYYY-MM-DD``
    string supplied by the caller (kept as an argument so this stays pure).
    """
    tag = version if version.startswith("v") else f"v{version}"
    lines = [f"## [{tag}] - {date}", ""]
    if bump:
        lines.append(f"_Bump: {bump}_")
        lines.append("")
    if entries:
        lines.extend(entry.render() for entry in entries)
    else:
        lines.append("- No changes recorded.")
    lines.append("")
    return "\n".join(lines)


def _normalise_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def insert_release(existing: str, version: str, section: str) -> str:
    """Insert ``section`` below the header, newest-first, idempotently.

    If ``version`` already has a section the content is returned unchanged so
    re-running the generator for the same release is a no-op.
    """
    tag = _normalise_tag(version)
    body = existing.strip("\n") if existing.strip() else CHANGELOG_HEADER.rstrip("\n")

    if any(found == tag for found in _VERSION_HEADING_RE.findall(body)):
        return existing if existing.endswith("\n") else existing + "\n"

    # Split the header (everything before the first version heading) from the
    # existing release sections so the new one lands directly under the header.
    first = _VERSION_HEADING_RE.search(body)
    if first is None:
        header, rest = body, ""
    else:
        header, rest = body[: first.start()].rstrip("\n"), body[first.start() :].strip("\n")

    section_block = section.strip("\n")
    parts = [header, "", section_block]
    if rest:
        parts.extend(["", rest])
    return "\n".join(parts).rstrip("\n") + "\n"
