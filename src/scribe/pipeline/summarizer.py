"""Summarizer — codex CLI (ChatGPT Pro subscription).

MVP backend: shells out to `codex exec`. Auth is a transferred ~/.codex/auth.json
(no interactive login — see design doc). Notes from bring-up (2026-05-14):
  * gpt-5.4-nano/mini are NOT available via a ChatGPT-account codex; the model is
    whatever the container's codex config selects (gpt-5.x family).
  * reasoning_effort must be >= "low" — "minimal" is rejected by the API because
    codex's default tools (image_gen, web_search) require it.
  * `--dangerously-bypass-approvals-and-sandbox` is correct here: the scribe
    container is the sandbox, and a pure summarization call runs no shell commands.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scribe.config import settings

_PROMPT_TEMPLATE = Path(__file__).resolve().parents[1] / "prompts" / "transcript-summary.md"
_TAGS_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)


class SummarizeError(RuntimeError):
    pass


@dataclass
class SummaryResult:
    summary_md: str
    tags: list[str]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "transcript"


def summarize(
    transcript_md: str,
    *,
    title: str,
    transcript_slug: str | None = None,
    summary_date: dt.date | None = None,
) -> SummaryResult:
    """Produce a Russian analytical summary of `transcript_md` via codex CLI."""
    summary_date = summary_date or dt.date.today()
    transcript_slug = transcript_slug or _slugify(title)
    if not _PROMPT_TEMPLATE.is_file():
        raise SummarizeError(f"prompt template missing: {_PROMPT_TEMPLATE}")
    prompt = (
        _PROMPT_TEMPLATE.read_text(encoding="utf-8")
        .replace("{date}", summary_date.isoformat())
        .replace("{transcript_slug}", transcript_slug)
        + "\n\nTranscript to summarize:\n\n"
        + transcript_md
    )

    with tempfile.TemporaryDirectory(prefix="scribe-codex-") as tmp:
        out_file = Path(tmp) / "summary.md"
        cmd = [
            settings.codex_bin, "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c", f"model_reasoning_effort={settings.codex_reasoning}",
            "-o", str(out_file),
        ]
        if settings.codex_model:
            cmd += ["-m", settings.codex_model]
        cmd += ["-"]  # read the prompt from stdin
        try:
            proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise SummarizeError("codex exec timed out after 600s") from exc
        if proc.returncode != 0 or not out_file.is_file():
            raise SummarizeError(
                f"codex exec failed (rc={proc.returncode}):\n{(proc.stderr or proc.stdout)[-2000:]}"
            )
        summary_md = out_file.read_text(encoding="utf-8").strip()

    if not summary_md:
        raise SummarizeError("codex exec produced an empty summary")
    # strip accidental ``` fences wrapping the whole output
    if summary_md.startswith("```"):
        lines = summary_md.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            summary_md = "\n".join(lines[1:-1]).strip()

    tags: list[str] = []
    match = _TAGS_RE.search(summary_md)
    if match:
        tags = [t.strip().strip("\"'") for t in match.group(1).split(",") if t.strip()]
    return SummaryResult(summary_md=summary_md, tags=tags)
