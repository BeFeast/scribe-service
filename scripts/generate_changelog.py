#!/usr/bin/env python3
"""Generate / update ``CHANGELOG.md`` for a release.

On each release this collects the merged pull-request titles in the tag range
and prepends a dated ``vX.Y.Z`` section to ``CHANGELOG.md`` (newest first). It is
idempotent: re-running for an already-recorded version is a no-op.

Usage (run from the repo root via uv):

    uv run python scripts/generate_changelog.py vX.Y.Z [--bump patch] \
        [--from <previous-tag>] [--date YYYY-MM-DD] [--dry-run]

If ``--from`` is omitted the most recent tag reachable before HEAD is used; if no
previous tag exists the whole history is summarised. If ``--date`` is omitted the
current UTC date is used.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from pathlib import Path

# Allow running as a plain script without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scribe.release.changelog import (  # noqa: E402
    CHANGELOG_HEADER,
    insert_release,
    parse_pr_titles,
    render_release_section,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _previous_tag(version: str) -> str | None:
    """Most recent tag before HEAD, ignoring the release tag being cut."""
    try:
        tags = _git("tag", "--sort=-creatordate").splitlines()
    except subprocess.CalledProcessError:
        return None
    for tag in tags:
        if tag and tag != version:
            return tag
    return None


def _collect_log(from_ref: str | None) -> str:
    rev_range = f"{from_ref}..HEAD" if from_ref else "HEAD"
    return _git("log", rev_range, "--no-merges", "--pretty=%s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate CHANGELOG.md for a release.")
    parser.add_argument("version", help="Release version, e.g. v1.2.3")
    parser.add_argument("--bump", choices=["major", "minor", "patch"], default=None)
    parser.add_argument("--from", dest="from_ref", default=None, help="Previous tag/ref (default: latest tag)")
    parser.add_argument("--date", default=None, help="Release date YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Print the new file to stdout, do not write")
    args = parser.parse_args(argv)

    version = args.version if args.version.startswith("v") else f"v{args.version}"
    date = args.date or _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")
    from_ref = args.from_ref or _previous_tag(version)

    entries = parse_pr_titles(_collect_log(from_ref))
    section = render_release_section(version, date, entries, bump=args.bump)

    existing = CHANGELOG_PATH.read_text(encoding="utf-8") if CHANGELOG_PATH.exists() else CHANGELOG_HEADER
    updated = insert_release(existing, version, section)

    if args.dry_run:
        sys.stdout.write(updated)
        return 0

    CHANGELOG_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated {CHANGELOG_PATH.relative_to(REPO_ROOT)} with {version} ({len(entries)} change(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
