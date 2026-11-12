"""Copy for AI (#489): render a transcript as an agent-friendly text document.

Pure functions, no DB access: the `/share/{token}` route loads the share link,
transcript and job, then calls these helpers to build the response body.

The document is a short plain-text header (``Key: value`` lines), an empty
line, a ``---`` separator, an empty line, and the spoken transcript text.
The empty lines around ``---`` matter: without them the ``Note:`` line right
above ``---`` renders as a level-2 Markdown heading in the ``.md`` variant.

Privacy rules: the raw ``job.url`` is never printed (uploads use
``upload:<filename>``; Telegram links can point into private chats), and
internal fields (``Source audio``, provider, cost, owner, ``video_id``) never
reach the output.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from scribe.pipeline.titles import _ENGAGEMENT_PREFIX
from scribe.source_links import source_link_for_url

if TYPE_CHECKING:
    from scribe.db.models import Job, Transcript

# Maximum characters per `?part=N` chunk. Module-level so tests can shrink it.
AI_PART_CHARS = 100_000

NOTE = (
    "This is a raw automatic speech-to-text transcript with no summary or editing; "
    "names and terms may be misrecognized. Use it as source material."
)

_TRANSCRIPT_MARKER = re.compile(r"^##[ \t]+Transcript[ \t]*$", re.MULTILINE)
# A sentence end: ASCII/ellipsis terminator plus optional closing quotes or
# brackets, followed by whitespace; or a CJK full-width terminator, which
# needs no trailing whitespace.
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]»]*(?=\s)|[。！？]")
_WHITESPACE = re.compile(r"\s")
_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}


def ai_transcript_body(transcript_md: str | None) -> str:
    """Return the spoken text of ``transcript_md`` without its rendered header.

    Normal rows carry ``# title`` / ``## Metadata`` / ``## Transcript``; only
    the text after ``## Transcript`` is kept. Legacy rows without the marker
    drop a leading ``# title`` line and a ``## Metadata`` bullet block.
    """
    text = transcript_md or ""
    marker = _TRANSCRIPT_MARKER.search(text)
    if marker is not None:
        return text[marker.end() :].strip()

    lines = text.strip().splitlines()
    i = 0
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "## Metadata":
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("- "):
            i += 1
    return "\n".join(lines[i:]).strip()


EMPTY_TRANSCRIPT = "[No speech was detected in the audio, so the transcript is empty.]"
# A caption must add this much beyond the display title to be worth serving.
_POST_TEXT_MIN_EXTRA = 20


def ai_post_text(transcript: Transcript) -> str | None:
    """The author's post caption kept in ``original_title`` (social reels).

    For reels the caption often carries the content while the audio is music
    or silence. Engagement counters and a trailing ``| <author>`` are dropped;
    a caption that only restates the display title is not served.
    """
    raw = " ".join((transcript.original_title or "").split())
    if not raw:
        return None
    text = _ENGAGEMENT_PREFIX.sub("", raw).strip()
    author = (transcript.author_name or "").strip()
    if author and text.casefold().endswith(("| " + author).casefold()):
        text = text[: -len(author)].rstrip().rstrip("|").rstrip()
    title = (transcript.title or "").strip()
    if not text or text == title or len(text) < len(title) + _POST_TEXT_MIN_EXTRA:
        return None
    return text


def ai_document_body(transcript: Transcript) -> str:
    """Body served to agents: post caption (when useful) plus the transcript."""
    spoken = ai_transcript_body(transcript.transcript_md) or EMPTY_TRANSCRIPT
    post = ai_post_text(transcript)
    if post is None:
        return spoken
    return f"## Post text\n\n{post}\n\n## Transcript\n\n{spoken}"


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _is_upload(transcript: Transcript, job: Job | None) -> bool:
    if (transcript.source_platform or "").lower() == "upload":
        return True
    if job is not None and (job.url or "").startswith("upload:"):
        return True
    return (transcript.video_id or "").startswith("upload:")


def _is_private_telegram(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in _TELEGRAM_HOSTS and (parsed.path == "/c" or parsed.path.startswith("/c/"))


def ai_source_value(transcript: Transcript, job: Job | None) -> str | None:
    """`Source` header value; None when it must be omitted.

    Uploads print ``uploaded file``. Only http(s) sources print their URL;
    other schemes (``tg:<file_id>`` and the like) are omitted. A Telegram
    private-chat link prints a label, never the URL.
    """
    if _is_upload(transcript, job):
        return "uploaded file"
    link = source_link_for_url(job.url if job is not None else None)
    if link is None:
        return None
    if urlparse(link.url).scheme.lower() not in {"http", "https"}:
        return None
    if _is_private_telegram(link.url):
        return "Telegram (private chat)"
    return link.url


def _platform_value(transcript: Transcript, job: Job | None) -> str | None:
    if _is_upload(transcript, job):
        return None
    if transcript.source_platform:
        return transcript.source_platform
    link = source_link_for_url(job.url if job is not None else None)
    return link.label if link is not None else None


def _author_value(transcript: Transcript) -> str | None:
    name = (transcript.author_name or "").strip()
    handle = (transcript.author_handle or "").strip()
    if name and handle and handle != name:
        return f"{name} ({handle})"
    return name or handle or None


def format_duration(seconds: int | float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_length(chars: int) -> str:
    return f"{chars:,} characters, ~{chars // 4:,} tokens"


def ai_header_fields(
    transcript: Transcript,
    job: Job | None,
    *,
    length: int,
    part: int | None = None,
    parts: int = 1,
    part_urls: Sequence[str] = (),
    next_url: str | None = None,
) -> list[tuple[str, str]]:
    """Ordered header fields; empty values are omitted.

    ``length`` is the character count of the body actually served. On a part
    response (``part`` set) the header carries ``Part: N of M`` and, unless it
    is the last part, ``Next part``. On an un-parted response for a long
    transcript (``parts > 1``) it lists every part URL instead.
    """
    created = transcript.created_at.date().isoformat() if transcript.created_at else None
    candidates: list[tuple[str, str | None]] = [
        ("Title", transcript.title),
        ("Source", ai_source_value(transcript, job)),
        ("Author", _author_value(transcript)),
        ("Platform", _platform_value(transcript, job)),
        ("Duration", format_duration(transcript.duration_seconds)),
        ("Language", transcript.lang),
        ("Transcribed", created),
        ("Length", format_length(length)),
    ]
    if part is not None:
        candidates.append(("Part", f"{part} of {parts}"))
        if part < parts:
            candidates.append(("Next part", next_url))
    elif parts > 1:
        candidates.append(("Parts available", str(parts)))
        for index, url in enumerate(part_urls, start=1):
            candidates.append((f"Part {index} of {parts}", url))
    candidates.append(("Timestamps", "none"))
    candidates.append(("Note", NOTE))

    fields: list[tuple[str, str]] = []
    for key, value in candidates:
        if value is None:
            continue
        line = _one_line(value)
        if line:
            fields.append((key, line))
    return fields


def render_ai_document(fields: Sequence[tuple[str, str]], body: str) -> str:
    header = "\n".join(f"{key}: {value}" for key, value in fields)
    return f"{header}\n\n---\n\n{body}\n"


def chunk_ai_body(body: str, limit: int | None = None) -> list[str]:
    """Split ``body`` into chunks of at most ``limit`` characters.

    Deterministic and greedy. Each chunk ends at the last sentence end in its
    window when that lies past the window's midpoint, otherwise at the last
    whitespace; a hard cut at ``limit`` happens only for text with no
    whitespace at all. Whitespace between chunks is dropped, so joining the
    chunks with single spaces reproduces the words of ``body``.
    """
    size = AI_PART_CHARS if limit is None else limit
    if size < 1:
        raise ValueError("limit must be positive")
    text = body.strip()
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    pos = 0
    end_of_text = len(text)
    while pos < end_of_text:
        if end_of_text - pos <= size:
            chunks.append(text[pos:])
            break
        # One character of lookahead so a sentence end or a space sitting
        # exactly at the window edge still counts as a clean cut.
        window = text[pos : pos + size + 1]
        cut = 0
        for match in _SENTENCE_END.finditer(window):
            if match.end() <= size:
                cut = match.end()
        if cut <= size // 2:
            cut = 0
            for match in _WHITESPACE.finditer(window):
                if match.start() > 0:
                    cut = match.start()
        if cut <= 0:
            cut = size
        chunk = text[pos : pos + cut].rstrip()
        if chunk:
            chunks.append(chunk)
        pos += cut
        while pos < end_of_text and text[pos].isspace():
            pos += 1
    return chunks
