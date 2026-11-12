"""Deterministic display titles and bounded summary source links (no I/O)."""
from __future__ import annotations

import re

MAX_TITLE_LENGTH = 120
TRUNCATED_TITLE_LENGTH = 100
MAX_SOURCE_SLUG_LENGTH = 80

_COUNT = r"\d[\d,.]*(?:\s?[KMB])?"
_METRIC = rf"{_COUNT}\s+(?:views?|plays?|reactions?|likes?|comments?|shares?)"
# Require the pipe delimiter: a title about "100 views" is still a title.
_ENGAGEMENT_PREFIX = re.compile(rf"^(?:{_METRIC})(?:\s*[·•]\s*{_METRIC})*\s*\|\s*", re.I)
_FRONTMATTER = re.compile(r"\A\ufeff?\s*---\s*\n.*?\n(?:---|\.\.\.)\s*(?:\n|$)", re.S)
_GENERIC_HEADINGS = {
    "tldr", "summary", "кратко", "краткое содержание", "краткое резюме", "резюме",
    "overview", "key points", "key takeaways", "основные выводы", "выводы",
    "conclusion", "conclusions", "transcript", "транскрипт", "סיכום",
}


def _cut(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    prefix = value[:limit - 1]
    if " " in prefix and value[limit - 1] != " ":
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip(" ,.;:|-–—") + "…"


def normalize_title(raw: str, *, author: str | None = None) -> tuple[str, bool]:
    """Return (display title, was too long after metadata cleanup).

    Preserve unknown author suffixes and ordinary pipe-separated titles.
    A single overlong word is cut by characters as a bounded fallback.
    """
    title = " ".join(raw.split())
    title = _ENGAGEMENT_PREFIX.sub("", title) or title
    if author:
        body, sep, suffix = title.rpartition(" | ")
        if body and sep and suffix.casefold() == " ".join(author.split()).casefold():
            title = body.rstrip()
    was_long = len(title) > MAX_TITLE_LENGTH
    return (_cut(title, TRUNCATED_TITLE_LENGTH) if was_long else title), was_long


def summary_heading(markdown: str | None) -> str | None:
    """First useful Markdown heading, excluding frontmatter and fenced code."""
    body = _FRONTMATTER.sub("", markdown or "", count=1)
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = marker[1]
            elif stripped.startswith(fence[0] * len(fence)):
                fence = None
            continue
        if fence:
            continue
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        if not match:
            continue
        heading = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", match[1])
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = " ".join(re.sub(r"[*_`~]", "", heading).split()).strip()
        key = re.sub(r"[^\w\s]", "", heading.casefold()).strip()
        if heading and key not in _GENERIC_HEADINGS:
            return _cut(heading, MAX_TITLE_LENGTH)
    return None


def title_slug(title: str) -> str:
    """Unicode-safe filename stem with a hard bound, independent of tag slugs."""
    title, _ = normalize_title(title)
    slug = re.sub(r"[\W_]+", "-", title.casefold(), flags=re.UNICODE).strip("-")
    return slug[:MAX_SOURCE_SLUG_LENGTH].rstrip("-") or "transcript"


def summary_source(markdown: str | None, title: str) -> str | None:
    """Set source only inside YAML frontmatter; leave body wikilinks alone."""
    if not markdown:
        return markdown
    match = _FRONTMATTER.match(markdown)
    if not match:
        return markdown
    frontmatter = match[0]
    source = f'source: "[[{title_slug(title)}]]"'
    if re.search(r"^source\s*:", frontmatter, flags=re.M):
        frontmatter = re.sub(r"^source\s*:.*$", lambda _: source, frontmatter, flags=re.M)
    else:
        frontmatter = frontmatter.replace("\n", f"\n{source}\n", 1)
    return frontmatter + markdown[match.end():]
