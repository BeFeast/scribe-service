"""Summary via codex CLI. TODO(task#6).

codex CLI in-container, auth via transferred JSON on a volume mount.
Shell out to `codex` with gpt-5.4-nano/mini + ported prompt template.
"""


def summarize(transcript_md: str) -> dict:
    """Return {summary_md, tags, ...}."""
    raise NotImplementedError("task#6")
