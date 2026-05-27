"""Summary pipeline entry point — drives the configured provider fallback chain.

Behaviour:
  * Build the full prompt (template + transcript) once.
  * Iterate `settings.summary_providers` (codex → claude → freellmapi by default).
  * On `SummaryResult`: log success, apply codex-style tag normalisation,
    return. The first usable canonical result wins.
  * On `ProviderUsageLimitError` / `ProviderUnavailableError` /
    `ProviderTimeoutError`: log a structured `scribe.summary.provider_fallback`
    warning and try the next provider.
  * On any other `ProviderError`: log at error level, then continue.
  * If the chain is exhausted, raise `SummarizeError` whose message lists each
    attempt and its reason.

Telegram alert: the codex token-revoked alert only fires AFTER the whole chain
has failed. During a multi-day codex outage we don't want to spam operators on
every job — the alert message stays informative when it does fire.

The previous direct-codex implementation lives in `CodexProvider` now; this
module is the orchestration + prompt-templating layer on top.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.pipeline import prompts
from scribe.pipeline.summary_providers import (
    LockTimeoutError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsageLimitError,
    SummaryResult,
    _acquire_codex_lock,  # noqa: F401  (re-export for tests/test_summarizer.py)
    build_provider_chain,
)
from scribe.pipeline.summary_validator import validate_and_canonicalize  # noqa: F401  (re-export for callers)

log = logging.getLogger("scribe.summarizer")

# Re-exported so callers (and tests) can still write
# `from scribe.pipeline.summarizer import SummaryResult` / `ProviderError`.
__all__ = [
    "CodexTokenRevokedError",
    "LockTimeoutError",
    "ProviderError",
    "SummarizeError",
    "SummaryResult",
    "summarize",
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
    """Raised when the full provider chain has been exhausted. Message
    enumerates each attempted provider and the reason it failed so the worker
    log captures the diagnostic surface in one place."""


class CodexTokenRevokedError(SummarizeError):
    """Retained for type-import compatibility with callers that catch it
    specifically (e.g. older worker branches). The new chain raises a generic
    `SummarizeError` after exhausting all providers; if any provider in the
    chain reported a codex token revocation, the Telegram alert still fires
    and the operator still needs to re-login. Production code should catch
    `SummarizeError` going forward."""


# `subprocess` import retained as a module attribute so existing tests that
# do `monkeypatch.setattr(summarizer.subprocess, "run", fake_run)` keep
# patching the same module object that `CodexProvider` reaches at call time.
import subprocess  # noqa: E402,F401  (intentional position — see comment above)


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


# Token-revoked detection kept here so `tests/test_summarizer.py` can still
# reach `summarizer._is_token_revoked` — the canonical implementation lives in
# `summary_providers` and is reused here.
from scribe.pipeline.summary_providers import _is_token_revoked  # noqa: E402,F401


def _alert_token_revoked(stderr_tail: str) -> None:
    """Fire-and-forget admin Telegram alert. Called exactly once after the
    whole chain has failed AND codex was among the failures with a
    token-revocation signature. Codex stays broken until the operator
    re-logins, so subsequent jobs naturally re-fire the alert too."""
    msg = (
        "🚨 scribe: codex OAuth token revoked. Re-login required:\n"
        "  docker exec -it scribe codex login --device-auth\n\n"
        f"codex stderr tail:\n{stderr_tail[-400:]}"
    )
    send_admin_alert(msg)


def summarize(
    transcript_md: str,
    *,
    title: str,
    transcript_slug: str | None = None,
    summary_date: dt.date | None = None,
    lock_timeout: float | None = None,
    prompt_version: str | None = None,
    prompt_body: str | None = None,
) -> SummaryResult:
    """Produce a canonical analytical summary of `transcript_md`.

    Drives the configured provider fallback chain
    (`settings.summary_providers`). Returns the first canonical
    `SummaryResult`. Raises `SummarizeError` only when every provider failed.
    """
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
    prompt = (
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

    providers = build_provider_chain(settings, lock_timeout=lock_timeout)
    if not providers:
        raise SummarizeError("no summary providers configured")

    attempts: list[tuple[str, str]] = []
    token_revoked_stderr: str | None = None
    lock_timeout_error: LockTimeoutError | None = None

    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        t_start = time.monotonic()
        try:
            result = provider.summarize(prompt)
        except LockTimeoutError as exc:
            # API-handler path: surface the timeout to the caller verbatim
            # rather than fading through. Don't swallow into a generic
            # SummarizeError — the route maps this to a 503-ish "try again
            # later" response.
            lock_timeout_error = exc
            break
        except ProviderUsageLimitError as exc:
            log.warning(
                "scribe.summary.provider_fallback",
                extra={
                    "provider": name,
                    "reason": "usage_limit",
                    "details": exc.details,
                    "elapsed_s": round(time.monotonic() - t_start, 3),
                },
            )
            attempts.append((name, f"usage_limit: {exc.details}"))
            continue
        except ProviderUnavailableError as exc:
            log.warning(
                "scribe.summary.provider_fallback",
                extra={
                    "provider": name,
                    "reason": "unavailable",
                    "details": exc.details,
                    "elapsed_s": round(time.monotonic() - t_start, 3),
                },
            )
            attempts.append((name, f"unavailable: {exc.details}"))
            stderr_tail = getattr(exc, "stderr_tail", "")
            if name == "codex" and stderr_tail and _is_token_revoked(stderr_tail):
                token_revoked_stderr = stderr_tail
            continue
        except ProviderTimeoutError as exc:
            log.warning(
                "scribe.summary.provider_fallback",
                extra={
                    "provider": name,
                    "reason": "timeout",
                    "details": exc.details,
                    "elapsed_s": round(time.monotonic() - t_start, 3),
                },
            )
            attempts.append((name, f"timeout: {exc.details}"))
            continue
        except ProviderError as exc:
            log.error(
                "scribe.summary.provider_fallback",
                extra={
                    "provider": name,
                    "reason": exc.reason,
                    "details": exc.details,
                    "elapsed_s": round(time.monotonic() - t_start, 3),
                },
            )
            attempts.append((name, f"{exc.reason}: {exc.details}"))
            continue
        log.info(
            "scribe.summary.provider_success",
            extra={
                "provider": name,
                "elapsed_s": round(time.monotonic() - t_start, 3),
                "tags": result.tags,
            },
        )
        return SummaryResult(
            summary_md=result.summary_md,
            tags=_normalize_tags(result.tags),
            short_description=result.short_description,
        )

    if lock_timeout_error is not None:
        raise lock_timeout_error

    # Chain exhausted. Fire the Telegram alert exactly once if codex flagged
    # a revoked token along the way.
    if token_revoked_stderr is not None:
        _alert_token_revoked(token_revoked_stderr)

    summary_lines = "; ".join(f"{n} -> {r}" for n, r in attempts)
    raise SummarizeError(
        f"all summary providers failed: {summary_lines}"
    )
