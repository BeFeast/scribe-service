"""Summary provider chain.

Each LLM backend (codex, claude, freellmapi-served models) implements the
`SummaryProvider` protocol: its `summarize()` produces raw markdown, runs the
output through `validate_and_canonicalize`, and either returns a clean
`SummaryResult` or raises `ProviderError`. `summarize_with_chain` iterates the
providers in order, catches `ProviderError` (treating a shape-invalid response
identically to a timeout), and falls through to the next provider.

The codex backend currently lives in `scribe.pipeline.summarizer.summarize`;
this module defines the shared contract additional backends will plug into.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from scribe.pipeline.summary_validator import (
    ProviderError,
    SummaryResult,
    validate_and_canonicalize,
)

__all__ = [
    "ProviderError",
    "SummaryProvider",
    "SummaryResult",
    "summarize_with_chain",
    "validate_and_canonicalize",
]

log = logging.getLogger("scribe.summary_providers")


@runtime_checkable
class SummaryProvider(Protocol):
    """A provider must expose a stable `name` for telemetry plus `summarize`.

    `summarize` must call `validate_and_canonicalize` on the raw LLM output
    before returning, and raise `ProviderError` for any unrecoverable
    response shape.
    """

    name: str

    def summarize(self, prompt: str) -> SummaryResult: ...


def summarize_with_chain(
    providers: list[SummaryProvider], prompt: str
) -> SummaryResult:
    """Try each provider in order. Catches `ProviderError` and advances; any
    other exception (auth, timeout, runtime error) propagates immediately.

    Raises `ProviderError(reason="chain_exhausted")` if every provider
    raised a shape error, or `ProviderError(reason="no_providers")` if the
    chain is empty.
    """
    if not providers:
        raise ProviderError(reason="no_providers", details="empty provider chain")

    last_error: ProviderError | None = None
    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        try:
            return provider.summarize(prompt)
        except ProviderError as exc:
            log.warning(
                "summary provider %s failed shape validation: %s",
                name,
                exc.details or exc.reason,
            )
            last_error = exc
            continue

    assert last_error is not None  # loop ran at least once
    raise ProviderError(
        reason="chain_exhausted",
        details=f"all providers failed; last={last_error.reason}: {last_error.details}",
    )
