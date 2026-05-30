"""Inject author/platform keys into the leading YAML frontmatter of summary_md.

Provider summaries already carry `tags` and `short_description` in a leading
`---` frontmatter block (see scribe.pipeline.summary_validator). The Properties
panel parses that block on the SPA, so the simplest way to surface video author
metadata is to add the keys here, on persist, instead of changing the prompt.

This is a small, deterministic rewrite — no YAML library needed:

  1. If the markdown has no leading `---` block, prepend a fresh one.
  2. Otherwise, walk the existing keys; replace `author`/`author_handle`/
     `author_url`/`platform` if they already exist (re-summarize path), else
     append them above the closing `---`.

All inputs are optional; whatever is `None` or empty is skipped.
"""
from __future__ import annotations


def _quote(value: str) -> str:
    """Quote a YAML scalar that may contain spaces, colons, or quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _kv_lines(
    *,
    author_name: str | None,
    author_handle: str | None,
    author_url: str | None,
    source_platform: str | None,
) -> list[tuple[str, str]]:
    """Return [(key, formatted_value)] for each non-empty author field."""
    pairs: list[tuple[str, str]] = []
    if author_name:
        pairs.append(("author", _quote(author_name)))
    if author_handle:
        pairs.append(("author_handle", _quote(author_handle)))
    if author_url:
        pairs.append(("author_url", _quote(author_url)))
    if source_platform:
        pairs.append(("platform", _quote(source_platform)))
    return pairs


def inject_author_frontmatter(
    summary_md: str,
    *,
    author_name: str | None = None,
    author_handle: str | None = None,
    author_url: str | None = None,
    source_platform: str | None = None,
) -> str:
    """Return summary_md with author/platform keys inserted into leading YAML.

    Idempotent on the same inputs (re-summarize replays). Pass-through when
    every field is None/empty.
    """
    pairs = _kv_lines(
        author_name=author_name,
        author_handle=author_handle,
        author_url=author_url,
        source_platform=source_platform,
    )
    if not pairs:
        return summary_md
    keys_to_set = {key for key, _ in pairs}

    text = summary_md or ""
    if not text.startswith("---"):
        # No frontmatter — wrap a fresh block before the body.
        block = "\n".join(f"{k}: {v}" for k, v in pairs)
        body = text.lstrip("\n")
        return f"---\n{block}\n---\n\n{body}" if body else f"---\n{block}\n---\n"

    # Locate closing fence. Body lines start at first `---` line after index 0.
    lines = text.split("\n")
    closing = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing = idx
            break
    if closing is None:
        # Malformed — leave alone, the validator handles repair elsewhere.
        return summary_md

    fm_lines = lines[1:closing]
    new_fm: list[str] = []
    for line in fm_lines:
        stripped = line.lstrip()
        # Match `key:` at column 0, no nested keys here.
        head, sep, _ = stripped.partition(":")
        if sep and head and head in keys_to_set and not line.startswith(" "):
            # Skip — we will re-emit at end with current value.
            continue
        new_fm.append(line)
    # Append every requested key so re-summarize always reflects the latest.
    for key, value in pairs:
        new_fm.append(f"{key}: {value}")

    rebuilt = "---\n" + "\n".join(new_fm).strip("\n") + "\n---"
    body_tail = "\n".join(lines[closing + 1 :])
    if body_tail:
        return rebuilt + "\n" + body_tail
    return rebuilt + "\n"
