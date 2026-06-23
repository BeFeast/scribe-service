"""Map-reduce summarization for oversized transcripts (#382).

Payload-limited summary backends (notably `freellmapi`, which returns
`413 PayloadTooLargeError`) cannot accept a long transcript + prompt template
in a single `/chat/completions` body. This module summarises such inputs in
two passes:

  * **map** — split the transcript on paragraph/sentence boundaries (with a
    small overlap so a thought straddling a boundary is not lost), and ask the
    provider to extract the key points of each chunk via its raw `complete()`
    call. Each map prompt stays well under the backend's payload limit.
  * **reduce** — concatenate the ordered partial summaries and run a final
    pass through the *same* provider using the original prompt template, so the
    result is a single cohesive summary in the canonical frontmatter shape.

The reduce input (instructions + partial summaries) is normally far smaller
than the transcript, but a transcript split into very many chunks can still
overflow. In that last-resort case the partials are truncated to fit and an
explicit `> [truncated: transcript exceeded N chars]` marker is injected into
the summary body so the degradation is visible rather than silent.

`map_reduce_summarize` is provider-agnostic: it drives any object exposing the
`complete(prompt) -> str` raw-completion method (every concrete provider in
`scribe.pipeline.summary_providers` does), so the single chunking path serves
the whole fallback chain rather than living inside one provider.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

from scribe.config import Settings
from scribe.config import settings as default_settings
from scribe.obs import metrics
from scribe.pipeline.summary_validator import (
    ProviderError,
    SummaryResult,
    validate_and_canonicalize,
)

log = logging.getLogger("scribe.summary_providers")

_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
_FRONTMATTER_RE = re.compile(r"\A(---\n.*?\n---\n)(?P<body>.*)\Z", re.DOTALL)

# Floor on the clamped chunk size so a pathological config (e.g. chunk_chars=1)
# cannot fan out into thousands of one-character map calls.
_MIN_CHUNK_CHARS = 1_000

_MAP_PROMPT = (
    "You are summarizing one part of a longer transcript that was split into "
    "{total} parts because it is too long to process at once. This is part "
    "{index} of {total}.\n\n"
    "Extract the key points, facts, names, numbers and conclusions from this "
    "part as concise bullet points in the transcript's language. Do not add a "
    "preamble, frontmatter, or closing remarks — output only the bullet "
    "points.\n\n"
    "Transcript part {index} of {total}:\n\n{chunk}"
)

_REDUCE_NOTE = (
    "The text below is a set of ordered partial summaries of a long transcript "
    "that was split into {total} chunks because it was too long to process at "
    "once. Treat them together as the full transcript and produce the single "
    "cohesive summary described above. Do not mention that the transcript was "
    "chunked."
)


@runtime_checkable
class _Completer(Protocol):
    """Minimal provider surface used by map-reduce: a stable name plus a raw
    `complete()` that returns unvalidated provider output."""

    name: str

    def complete(self, prompt: str) -> str: ...


def split_transcript(
    text: str, *, chunk_chars: int, overlap_chars: int
) -> list[str]:
    """Split `text` into chunks of at most `chunk_chars`, preferring paragraph
    then sentence boundaries, with up to `overlap_chars` of trailing context
    repeated at the start of the next chunk.

    Returns a single-element list when the text already fits (or chunking is
    disabled via `chunk_chars <= 0`), so callers can treat the result
    uniformly.
    """
    text = (text or "").strip()
    if not text:
        return []
    if chunk_chars <= 0 or len(text) <= chunk_chars:
        return [text]

    overlap = max(0, min(overlap_chars, chunk_chars // 2))
    units = _atomic_units(text, chunk_chars)

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0  # running length of `cur` including the "\n\n" separators

    for unit in units:
        extra = len(unit) + (2 if cur else 0)
        if cur and cur_len + extra > chunk_chars:
            chunks.append("\n\n".join(cur))
            cur = _overlap_tail(cur, overlap)
            cur_len = _joined_len(cur)
            extra = len(unit) + (2 if cur else 0)
        cur.append(unit)
        cur_len += extra
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def map_reduce_summarize(
    provider: _Completer,
    *,
    instructions: str,
    transcript: str,
    settings: Settings | None = None,
) -> SummaryResult:
    """Summarise an oversized transcript via map-reduce through `provider`.

    `instructions` is the rendered prompt template (no transcript appended);
    it is reused verbatim for the final reduce pass so the merged summary keeps
    the canonical frontmatter shape. `transcript` is the raw transcript that is
    chunked for the map pass.

    Raises `ProviderError` if the provider fails on any map or reduce call (so
    the fallback chain advances exactly as for a single-pass failure) or if the
    reduce output cannot be canonicalised.
    """
    s = settings or default_settings
    chunk_chars = max(_MIN_CHUNK_CHARS, s.summary_map_reduce_chunk_chars)
    chunks = split_transcript(
        transcript,
        chunk_chars=chunk_chars,
        overlap_chars=s.summary_map_reduce_overlap_chars,
    )
    if not chunks:
        raise ProviderError(reason="empty_response", details="empty transcript")

    name = getattr(provider, "name", type(provider).__name__)
    total = len(chunks)
    metrics.summary_map_reduce_chunks.observe(total)

    # map: summarise each chunk into partial bullet points.
    partials: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        map_prompt = _MAP_PROMPT.format(index=index, total=total, chunk=chunk)
        try:
            partial = provider.complete(map_prompt).strip()
        except ProviderError:
            metrics.summary_map_reduce_total.labels(provider=name, result="failed").inc()
            raise
        if partial:
            partials.append(f"## Part {index} of {total}\n\n{partial}")

    if not partials:
        metrics.summary_map_reduce_total.labels(provider=name, result="failed").inc()
        raise ProviderError(
            reason="empty_response",
            details="all map chunks produced empty partial summaries",
        )

    # reduce: merge partials into one canonical summary, truncating only if the
    # combined partials would themselves overflow the threshold.
    combined = "\n\n".join(partials)
    note = _REDUCE_NOTE.format(total=total)
    head = f"{instructions}\n\n{note}\n\n"
    threshold = s.summary_map_reduce_chars
    truncated = False
    if threshold > 0 and len(head) + len(combined) > threshold:
        budget = max(0, threshold - len(head))
        combined = combined[:budget].rstrip()
        truncated = True

    reduce_prompt = f"{head}{combined}"
    try:
        raw = provider.complete(reduce_prompt).strip()
    except ProviderError:
        metrics.summary_map_reduce_total.labels(provider=name, result="failed").inc()
        raise

    result = validate_and_canonicalize(raw)
    if truncated:
        marker = f"> [truncated: transcript exceeded {threshold} chars]"
        result = SummaryResult(
            summary_md=_inject_marker(result.summary_md, marker),
            tags=result.tags,
            short_description=result.short_description,
        )

    outcome = "truncated" if truncated else "success"
    metrics.summary_map_reduce_total.labels(provider=name, result=outcome).inc()
    log.info(
        "scribe.summary.map_reduce chunks=%d truncated=%s provider=%s",
        total,
        truncated,
        name,
        extra={"provider": name, "chunks": total, "truncated": truncated},
    )
    return result


# ---------- helpers -----------------------------------------------------------


def _atomic_units(text: str, limit: int) -> list[str]:
    """Break `text` into segments each no longer than `limit`, splitting on
    paragraph then sentence boundaries, hard-splitting only as a last resort."""
    units: list[str] = []
    for para in _PARAGRAPH_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= limit:
            units.append(para)
            continue
        for sentence in _SENTENCE_RE.split(para):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= limit:
                units.append(sentence)
                continue
            for start in range(0, len(sentence), limit):
                units.append(sentence[start : start + limit])
    return units


def _overlap_tail(units: list[str], overlap: int) -> list[str]:
    """Return the trailing units of a flushed chunk whose joined length fits in
    `overlap`, to seed the next chunk. Empty when even the last unit overflows
    the overlap budget (so a big unit is never duplicated)."""
    if overlap <= 0:
        return []
    tail: list[str] = []
    tail_len = 0
    for unit in reversed(units):
        extra = len(unit) + (2 if tail else 0)
        if tail_len + extra > overlap:
            break
        tail.insert(0, unit)
        tail_len += extra
    return tail


def _joined_len(units: list[str]) -> int:
    if not units:
        return 0
    return sum(len(u) for u in units) + 2 * (len(units) - 1)


def _inject_marker(summary_md: str, marker: str) -> str:
    """Insert `marker` as the first body line, just after the leading
    frontmatter block (or at the very top when there is none)."""
    m = _FRONTMATTER_RE.match(summary_md)
    if not m:
        return f"{marker}\n\n{summary_md}"
    head = m.group(1)
    body = m.group("body").lstrip("\n")
    return f"{head}\n{marker}\n\n{body}"
