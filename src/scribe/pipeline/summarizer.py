"""Summarizer — provider-chain entrypoint (default: codex → freellmapi).

`summarize()` builds the configured provider chain (see
`scribe.pipeline.summary_providers`) and translates `ProviderError` outcomes
into the historical `SummarizeError` / `CodexTokenRevokedError` exceptions the
worker still raises to user-visible callers.

Resilience (PRD §4.4):
  * Each provider's `summarize()` is wrapped by the chain's circuit breaker so
    a sustained outage on one backend doesn't burn timeout budget on every job.
  * `CodexProvider` keeps the `fcntl.flock(codex_lock_path)` serialisation —
    ChatGPT-account OAuth refresh tokens are single-use and concurrent codex
    runs race them into mutual revocation. The lock acquire is bounded by
    `codex_lock_wait_timeout_secs` (issue #352): a second worker that can't get
    the lock falls through to the next provider instead of stalling for the full
    codex timeout, and `scribe_codex_lock_wait_seconds` exposes the contention.
  * If the whole chain fails and codex's last failure was a token-revoked
    signature, scribe fires a one-shot admin Telegram alert with the codex
    stderr tail so the operator knows to re-login. The alert is suppressed if
    codex was rotated out by the chain successfully — we don't want to spam
    Telegram every job during a multi-day codex outage when the fallback
    providers are absorbing the load.

Bring-up notes (2026-05-14):
  * gpt-5.4-nano/mini are NOT available via a ChatGPT-account codex; the model
    is whatever the container's codex config selects (gpt-5.x family).
  * codex `reasoning_effort` must be >= "low" — "minimal" is rejected by the
    API because codex's default tools (image_gen, web_search) require it.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.pipeline import prompts
from scribe.pipeline.summary_providers import (
    ProviderError,
    SummaryResult,
    build_provider_chain,
    summarize_with_chain,
)
from scribe.pipeline.summary_validator import validate_and_canonicalize

log = logging.getLogger("scribe.summarizer")

# Re-exported so callers (and tests) can still write
# `from scribe.pipeline.summarizer import SummaryResult` / `ProviderError`.
__all__ = [
    "CodexTokenRevokedError",
    "ProviderError",
    "SummarizeError",
    "SummaryResult",
    "summarize",
    "validate_and_canonicalize",
]

_TAG_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TAG_REPLACEMENTS = {
    "bytovaya-scena": "everyday-scene",
    "detskaya-rech": "child-speech",
    "dok-stancii": "docking-stations",
    "internet-kultura": "internet-culture",
    "intuiciya": "intuition",
    "karera": "career",
    "periferia": "peripherals",
    "plagin": "plugins",
    "poisk": "search",
    "pokupka": "buying-guide",
    "predprinimatelstvo": "entrepreneurship",
    "produktivnost": "productivity",
    "smertnost": "mortality",
    "stiv-dzhobs": "steve-jobs",
    "usb-huby": "usb-hubs",
}
_SHORT_DESCRIPTION_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
}
_REJECTED_TAGS = {
    "auto-generated",
    "example",
    "tag1",
    "tag2",
    "tag3",
    "transcript",
}


class SummarizeError(RuntimeError):
    pass


class CodexTokenRevokedError(SummarizeError):
    """codex OAuth token is revoked / un-refreshable. Surfaced when the whole
    fallback chain failed AND codex's last failure was a token-revoked
    signature, so the operator knows the underlying cause is codex auth."""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "transcript"


def _normalize_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        slug = _slugify(value)
        slug = _TAG_REPLACEMENTS.get(slug, slug)
        if slug in _REJECTED_TAGS or not _TAG_SLUG_RE.fullmatch(slug):
            continue
        if slug not in seen:
            tags.append(slug)
            seen.add(slug)
    return tags


def _short_description_language_name(code: str) -> str:
    return _SHORT_DESCRIPTION_LANGUAGE_NAMES.get(code, "Russian")


def _alert_token_revoked(stderr_tail: str) -> None:
    """Fire-and-forget admin Telegram alert. Called once after the whole chain
    has failed AND codex's last failure was a token-revoked signature, so
    operators are not spammed during multi-day codex outages where the
    fallback providers are absorbing every job."""
    msg = (
        "🚨 scribe: codex OAuth token revoked. Re-login required:\n"
        "  docker exec -it scribe codex login --device-auth\n\n"
        f"codex stderr tail:\n{stderr_tail[-400:]}"
    )
    send_admin_alert(msg)


def _build_prompt(
    transcript_md: str,
    *,
    title: str,
    transcript_slug: str | None,
    summary_date: dt.date | None,
    prompt_version: str | None,
    prompt_body: str | None,
) -> str:
    summary_date = summary_date or dt.date.today()
    transcript_slug = transcript_slug or _slugify(title)
    try:
        if prompt_body is not None:
            template = prompt_body
        elif prompt_version:
            template = prompts.read_prompt(prompt_version)
        else:
            template = prompts.read_active_prompt()[1]
    except prompts.PromptError as exc:
        raise SummarizeError(str(exc)) from exc
    return (
        template.replace("{date}", summary_date.isoformat())
        .replace("{transcript_slug}", transcript_slug)
        .replace("{short_description_language_code}", settings.short_description_language)
        .replace(
            "{short_description_language_name}",
            _short_description_language_name(settings.short_description_language),
        )
        + "\n\nTranscript to summarize:\n\n"
        + transcript_md
    )


def summarize(
    transcript_md: str,
    *,
    title: str,
    transcript_slug: str | None = None,
    summary_date: dt.date | None = None,
    prompt_version: str | None = None,
    prompt_body: str | None = None,
) -> SummaryResult:
    """Produce a summary of `transcript_md` via the configured provider chain.

    Tries each provider in `settings.summary_providers` order. Returns the
    first `SummaryResult` (with scribe tag normalisation applied on top).
    Raises `SummarizeError` if the whole chain fails; raises
    `CodexTokenRevokedError` when the whole chain failed and codex's last
    failure was a token-revoked signature.
    """
    prompt = _build_prompt(
        transcript_md,
        title=title,
        transcript_slug=transcript_slug,
        summary_date=summary_date,
        prompt_version=prompt_version,
        prompt_body=prompt_body,
    )

    try:
        providers = build_provider_chain()
    except ValueError as exc:
        raise SummarizeError(str(exc)) from exc

    attempts: list[tuple[str, str]] = []
    try:
        result = summarize_with_chain(providers, prompt, attempts=attempts)
    except ProviderError as exc:
        if exc.reason == "no_providers":
            raise SummarizeError(
                "no summary providers configured (set SCRIBE_SUMMARY_PROVIDERS)"
            ) from exc
        # Duck-typed: any provider in the chain may expose
        # `last_token_revoked_stderr` to signal an OAuth-token outage that
        # warrants an operator alert. Only CodexProvider sets it today.
        token_revoked_tail: str | None = None
        for provider in providers:
            tail = getattr(provider, "last_token_revoked_stderr", None)
            if tail:
                token_revoked_tail = tail
                break
        attempts_summary = "; ".join(
            f"{name}={outcome}" for name, outcome in (attempts or [])
        ) or exc.details
        message = f"all summary providers failed: {attempts_summary}"
        if token_revoked_tail is not None:
            _alert_token_revoked(token_revoked_tail)
            raise CodexTokenRevokedError(message) from exc
        raise SummarizeError(message) from exc

    # Apply scribe-content tag rules (transliteration, slug regex, rejected
    # placeholders) on top of the shape-only canonicalisation each provider
    # already performed before returning.
    return SummaryResult(
        summary_md=result.summary_md,
        tags=_normalize_tags(result.tags),
        short_description=result.short_description,
    )
