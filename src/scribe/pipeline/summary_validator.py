"""Per-provider summary output shape validator + canonicalization.

Each LLM provider (codex, claude, freellmapi-served models) returns markdown
that may deviate from the canonical shape the downstream parser expects:
YAML frontmatter with `tags` (list) and `short_description` (string), level-2
section headers, no prose outside the structured sections.

This module:
  * Parses the leading YAML frontmatter (or attempts a repair if it is
    missing but a block containing `tags:`/`short_description:` is present).
  * Coerces `tags` from a comma/space-separated string into a list when the
    model emitted a scalar.
  * Falls back to the first non-empty body line (<= 200 chars) when
    `short_description` is missing from the frontmatter.
  * Downshifts stray `#` (H1) section headers to `##` (H2), matching the
    canonical prompt template.
  * Raises `ProviderError(reason="shape_invalid", ...)` when the response
    cannot be repaired. The caller treats this identically to a timeout —
    the fallback chain advances to the next provider rather than failing the
    job after an apparently successful LLM call.

Scope: structural validation only. Tag taxonomy / content normalisation
(transliteration, slug rules, rejected-tag filtering) is the caller's
responsibility — see `scribe.pipeline.summarizer._normalize_tags`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SHORT_DESCRIPTION_MAX = 200

_LEADING_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(?P<body>.*?)\n---[ \t]*(?:\n|$)",
    re.DOTALL,
)
_BLOCK_SPLIT_RE = re.compile(r"\n[ \t]*\n")
_FRONTMATTER_KEY_RE = re.compile(r"^[ \t]*([A-Za-z_][\w-]*)[ \t]*:[ \t]*(.*)$")
_FRONTMATTER_HINT_RE = re.compile(
    r"^[ \t]*(?:tags|short_description)[ \t]*:", re.MULTILINE
)
_H1_LINE_RE = re.compile(r"^# (?=\S)")


class ProviderError(RuntimeError):
    """Provider returned an unrecoverable response. The fallback chain
    catches this exception and advances to the next provider."""

    def __init__(self, *, reason: str, details: str = "") -> None:
        msg = f"{reason}: {details}" if details else reason
        super().__init__(msg)
        self.reason = reason
        self.details = details


@dataclass
class SummaryResult:
    """Canonical summary payload returned by a provider after validation."""

    summary_md: str
    tags: list[str]
    short_description: str | None = None


def validate_and_canonicalize(markdown: str) -> SummaryResult:
    """Validate and canonicalise raw provider markdown.

    Returns a `SummaryResult` with the canonical `summary_md` (leading
    frontmatter, level-2 section headers) plus extracted `tags` and
    `short_description`. Raises `ProviderError(shape_invalid)` if the
    response cannot be made canonical.
    """
    md = _strip_outer_code_fence((markdown or "").strip())
    if not md:
        raise ProviderError(reason="shape_invalid", details="empty response")

    fm_text, body = _extract_frontmatter(md)
    if fm_text is None:
        raise ProviderError(
            reason="shape_invalid",
            details="no frontmatter block found",
        )

    frontmatter = _parse_frontmatter(fm_text)

    tags = _coerce_tags(frontmatter.get("tags"))
    if not tags:
        raise ProviderError(
            reason="shape_invalid",
            details="no usable tags in frontmatter",
        )

    short_description = _coerce_short_description(
        frontmatter.get("short_description"), body
    )
    if not short_description:
        raise ProviderError(
            reason="shape_invalid",
            details="no short_description and no body fallback",
        )

    body_canonical = _downshift_h1_headers(body).strip("\n")
    if not body_canonical.strip():
        raise ProviderError(reason="shape_invalid", details="empty body")

    summary_md = _rebuild_markdown(fm_text, tags, short_description, body_canonical)
    return SummaryResult(
        summary_md=summary_md,
        tags=tags,
        short_description=short_description,
    )


# ---------- helpers -----------------------------------------------------------


def _strip_outer_code_fence(md: str) -> str:
    if not md.startswith("```"):
        return md
    lines = md.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return md


def _extract_frontmatter(md: str) -> tuple[str | None, str]:
    """Return `(frontmatter_text, body)`.

    Primary path: leading `---\\n...\\n---` block. Repair path: split the
    document on blank lines and treat the first block that contains a
    `tags:` or `short_description:` key as frontmatter, stripping any `---`
    delimiters embedded in the block.
    """
    m = _LEADING_FRONTMATTER_RE.match(md)
    if m:
        return m.group("body"), md[m.end():]

    blocks = _BLOCK_SPLIT_RE.split(md)
    for idx, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped:
            continue
        if not _FRONTMATTER_HINT_RE.search(stripped):
            continue
        fm_text = re.sub(r"\A---[ \t]*\n", "", stripped)
        fm_text = re.sub(r"\n---[ \t]*\Z", "", fm_text).strip()
        body = "\n\n".join(blocks[idx + 1:])
        return fm_text, body
    return None, md


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML-ish parser for our flat frontmatter shape.

    Handles `key: value`, `key: "quoted"`, `key: [a, b, "c"]`. Other values
    are returned as raw stripped strings. Comment lines and blank lines are
    ignored.
    """
    out: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _FRONTMATTER_KEY_RE.match(line)
        if not m:
            continue
        key, raw_val = m.group(1), m.group(2).strip()
        out[key] = _parse_scalar(raw_val)
    return out


def _parse_scalar(value: str) -> Any:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(p.strip()) for p in _split_top_level_commas(inner) if p.strip()]
    return _unquote(value)


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _split_top_level_commas(s: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            buf.append(ch)
            quote = ch
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _coerce_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip().strip("\"'") for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return []
        return [
            _unquote(part).strip()
            for part in re.split(r"[,\s]+", cleaned)
            if part.strip()
        ]
    return []


def _coerce_short_description(value: Any, body: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("-") or line.startswith("```"):
            continue
        return line[:_SHORT_DESCRIPTION_MAX]
    return ""


def _downshift_h1_headers(body: str) -> str:
    """Replace `# Title` headers (H1) with `## Title` (H2). Other header
    levels are preserved. Lines inside fenced code blocks are not touched."""
    out: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _H1_LINE_RE.match(line):
            out.append("#" + line)
        else:
            out.append(line)
    return "\n".join(out)


def _rebuild_markdown(
    fm_text: str,
    tags: list[str],
    short_description: str,
    body: str,
) -> str:
    """Rebuild canonical document: leading frontmatter, body. The frontmatter
    block preserves any extra keys the model emitted (e.g. `date`, `source`)
    but rewrites `tags` as a list and `short_description` as a quoted string.
    """
    saw_tags = False
    saw_short = False
    new_lines: list[str] = []
    for raw_line in fm_text.splitlines():
        if not raw_line.strip():
            new_lines.append(raw_line)
            continue
        m = _FRONTMATTER_KEY_RE.match(raw_line)
        if not m:
            new_lines.append(raw_line)
            continue
        key = m.group(1)
        if key == "tags":
            new_lines.append(f"tags: [{', '.join(tags)}]")
            saw_tags = True
        elif key == "short_description":
            new_lines.append(
                f'short_description: "{_escape_double_quotes(short_description)}"'
            )
            saw_short = True
        else:
            new_lines.append(raw_line)
    if not saw_tags:
        new_lines.append(f"tags: [{', '.join(tags)}]")
    if not saw_short:
        new_lines.append(
            f'short_description: "{_escape_double_quotes(short_description)}"'
        )
    fm_block = "\n".join(new_lines).strip("\n")
    return f"---\n{fm_block}\n---\n\n{body}\n"


def _escape_double_quotes(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
