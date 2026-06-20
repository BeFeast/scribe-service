"""Transcription provider chain + per-provider circuit breaker.

Mirrors the summary fallback chain (`scribe.pipeline.summary_providers`): each
backend implements the `TranscribeProvider` protocol, and
`transcribe_with_chain` iterates the configured providers in order, consulting a
per-provider `CircuitBreaker` before each call. A trip-relevant failure
(`TranscribeProviderUnavailableError`, `TranscribeProviderUsageLimitError`,
`TranscribeProviderTimeoutError`) is caught and the chain advances to the next
provider; the result records which provider actually served the job.

The primary provider is Vast.ai GPU whisper (`whisper_client.transcribe`). When
Vast offers fail repeatedly (an outage, a thin market, driver mismatches) the
breaker trips and the chain skips Vast for the cooldown window, serving jobs
from the configured fallback instead. Fallbacks are opt-in and cost-capped:

  * `openai`        — hosted Whisper API. Billed per minute; a per-job estimate
                      is checked against a cap before the upload, and spend is
                      surfaced on its own metric line.
  * `local-whisper` — CPU `faster-whisper`. Slow but always available; needs no
                      GPU and no network. The dependency is optional — if it is
                      not importable the provider reports unavailable and the
                      chain advances.

The wall-clock guard `TranscribeTimeoutError` (the whole-job budget inside
`whisper_client.transcribe`) is *not* a fallthrough signal: a job that already
burned its entire transcription budget should fail rather than start an even
slower provider, so that exception propagates out of the chain unchanged.

Breaker state is in-process; a container restart resets every provider to
`closed`, matching the summary chain's documented contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from scribe.config import Settings
from scribe.config import settings as default_settings
from scribe.obs import metrics
from scribe.pipeline import whisper_client
from scribe.pipeline.summary_providers import CircuitBreaker
from scribe.pipeline.whisper_client import (
    TranscribeResult,
    TranscribeTimeoutError,
    WhisperError,
)

__all__ = [
    "CircuitBreaker",
    "LocalWhisperProvider",
    "OpenAITranscribeProvider",
    "PROVIDER_REGISTRY",
    "TranscribeChainError",
    "TranscribeProvider",
    "TranscribeProviderError",
    "TranscribeProviderTimeoutError",
    "TranscribeProviderUnavailableError",
    "TranscribeProviderUsageLimitError",
    "TranscribeRequest",
    "TranscribeResult",
    "VastProvider",
    "build_provider_chain",
    "get_breaker",
    "transcribe_with_chain",
]

log = logging.getLogger("scribe.transcribe_providers")

_STATE_VALUES = {"closed": 0, "half_open": 1, "tripped": 2}
_TRIP_RELEVANT_OUTCOMES = frozenset({"usage_limit", "unavailable", "timeout"})


class TranscribeProviderError(RuntimeError):
    """A transcription provider failed in a way the chain can absorb. The
    chain catches this (and its subclasses) and advances to the next
    provider, treating it like the summary chain treats `ProviderError`."""

    def __init__(self, *, reason: str, details: str = "") -> None:
        msg = f"{reason}: {details}" if details else reason
        super().__init__(msg)
        self.reason = reason
        self.details = details


class TranscribeProviderUsageLimitError(TranscribeProviderError):
    """Provider rejected the call due to a usage cap / cost cap / rate limit."""


class TranscribeProviderUnavailableError(TranscribeProviderError):
    """Provider is unavailable (Vast offers exhausted, 5xx, missing dep, ...)."""


class TranscribeProviderTimeoutError(TranscribeProviderError):
    """A single provider call exceeded its own wall-clock budget."""


class TranscribeChainError(RuntimeError):
    """Every configured provider failed or was skipped (breaker tripped)."""

    def __init__(
        self,
        *,
        reason: str,
        details: str = "",
        attempts: list[tuple[str, str]] | None = None,
    ) -> None:
        msg = f"{reason}: {details}" if details else reason
        super().__init__(msg)
        self.reason = reason
        self.details = details
        self.attempts = list(attempts or [])


@dataclass
class TranscribeRequest:
    """Everything a provider needs to transcribe one job's audio.

    `duration_seconds` is the yt-dlp duration hint (may be None) used by the
    hosted provider to estimate cost before uploading.
    """

    wav: Path
    title: str
    source_url: str
    model_size: str = "large-v3-turbo"
    compute_type: str = "float16"
    language: str = "auto"
    beam_size: int = 5
    duration_seconds: int | None = None


@runtime_checkable
class TranscribeProvider(Protocol):
    """A provider exposes a stable `name` for telemetry plus `transcribe`.

    `transcribe` returns a `TranscribeResult` or raises
    `TranscribeProviderError` for any recoverable failure (so the chain can
    fall through). Hard, non-recoverable conditions (the wall-clock guard)
    should propagate as their original exception type instead.
    """

    name: str

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult: ...


def _classify_error(exc: TranscribeProviderError) -> str:
    """Map a TranscribeProviderError to a metric/breaker outcome label."""
    if isinstance(exc, TranscribeProviderUsageLimitError):
        return "usage_limit"
    if isinstance(exc, TranscribeProviderTimeoutError):
        return "timeout"
    if isinstance(exc, TranscribeProviderUnavailableError):
        return "unavailable"
    return "error"


# ---------- circuit breaker registry -----------------------------------------

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    """Return the module-level breaker for `name`, creating it on first use.

    Configuration is read from `settings` at creation time; like the summary
    chain, in-flight breakers are not reconfigured if settings change later
    (restart-to-reset contract). The reused `CircuitBreaker` primitive lives in
    `summary_providers`, so its monotonic clock is `summary_providers._now`.
    """
    breaker = _breakers.get(name)
    if breaker is None:
        breaker = CircuitBreaker(
            name,
            window_secs=default_settings.transcribe_breaker_window_secs,
            threshold=default_settings.transcribe_breaker_threshold,
            cooldown_secs=default_settings.transcribe_breaker_cooldown_secs,
        )
        _breakers[name] = breaker
        metrics.transcribe_provider_state.labels(provider=name).set(
            _STATE_VALUES[breaker.state]
        )
    return breaker


def _reset_breakers_for_test() -> None:
    """Drop all in-process breakers. Test-only helper."""
    _breakers.clear()


def _publish_state(breaker: CircuitBreaker) -> None:
    metrics.transcribe_provider_state.labels(provider=breaker.name).set(
        _STATE_VALUES[breaker.state]
    )


def transcribe_with_chain(
    providers: list[TranscribeProvider],
    request: TranscribeRequest,
    *,
    attempts: list[tuple[str, str]] | None = None,
) -> TranscribeResult:
    """Try each provider in order, consulting its circuit breaker first.

    Tripped providers are skipped without a call. `TranscribeProviderError`
    (and subclasses) is caught and the chain advances; any other exception
    (notably the `TranscribeTimeoutError` wall-clock guard) propagates
    immediately. On success the result's `provider` field is stamped with the
    serving provider's name.

    Raises `TranscribeChainError(reason="chain_exhausted")` if every provider
    failed or was skipped, or `TranscribeChainError(reason="no_providers")` for
    an empty chain.
    """
    if not providers:
        raise TranscribeChainError(reason="no_providers", details="empty provider chain")

    local_attempts: list[tuple[str, str]] = attempts if attempts is not None else []
    last_error: TranscribeProviderError | None = None
    had_fallback = False

    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        breaker = get_breaker(name)
        mode = breaker.acquire()
        _publish_state(breaker)

        if mode == "skip":
            log.info(
                "scribe.transcribe.provider_skipped_tripped",
                extra={"provider": name, "breaker_state": breaker.state},
            )
            metrics.transcribe_provider_calls_total.labels(
                provider=name, result="skipped_tripped"
            ).inc()
            local_attempts.append((name, "skipped_tripped"))
            had_fallback = True
            continue

        try:
            result = provider.transcribe(request)
        except TranscribeProviderError as exc:
            outcome = _classify_error(exc)
            metrics.transcribe_provider_calls_total.labels(
                provider=name, result=outcome
            ).inc()
            breaker.record(outcome, mode=mode)
            _publish_state(breaker)
            log.warning(
                "scribe.transcribe.provider_fallback",
                extra={
                    "provider": name,
                    "outcome": outcome,
                    "reason": exc.reason,
                    "details": exc.details,
                },
            )
            local_attempts.append((name, f"{outcome}: {exc.details or exc.reason}"))
            last_error = exc
            had_fallback = True
            continue

        result.provider = name
        metrics.transcribe_provider_calls_total.labels(
            provider=name, result="success"
        ).inc()
        breaker.record("success", mode=mode)
        _publish_state(breaker)
        local_attempts.append((name, "success"))

        chain_label = "success_after_fallback" if had_fallback else "success_first"
        metrics.transcribe_chain_outcome_total.labels(outcome=chain_label).inc()
        log.info(
            "scribe.transcribe.provider_success",
            extra={"provider": name, "chain_outcome": chain_label},
        )
        return result

    metrics.transcribe_chain_outcome_total.labels(outcome="all_failed").inc()
    if last_error is None:
        details = "all providers skipped (tripped)"
    else:
        details = f"all providers failed; last={last_error.reason}: {last_error.details}"
    raise TranscribeChainError(
        reason="chain_exhausted", details=details, attempts=local_attempts
    )


# ---------- concrete provider implementations --------------------------------


def _render_transcript_md(
    *,
    title: str,
    source_url: str,
    source_audio: str,
    backend: str,
    detected_language: str,
    language_probability: float | None,
    duration: float | None,
    transcript_text: str,
) -> str:
    """Render transcript markdown matching the Vast remote script's shape so
    downstream parsing/summary is identical regardless of provider."""
    probability_text = "unknown" if language_probability is None else f"{language_probability:.3f}"
    duration_text = "unknown" if duration is None else f"{duration:.2f}s"
    return (
        f"# {title}\n\n"
        "## Metadata\n"
        f"- Source URL: {source_url}\n"
        f"- Source audio: {source_audio}\n"
        f"- Transcription model: {backend}\n"
        f"- Detected language: {detected_language}\n"
        f"- Language probability: {probability_text}\n"
        f"- Duration: {duration_text}\n\n"
        "## Transcript\n\n"
        f"{transcript_text}\n"
    )


class VastProvider:
    """Primary provider: Vast.ai GPU whisper via `whisper_client.transcribe`.

    Holds the worker's instance/lifecycle callbacks so the chain entrypoint
    keeps the exact Vast bookkeeping the worker relied on. `WhisperError`
    (offers exhausted, instance never ready, container failed) is translated
    into `TranscribeProviderUnavailableError` so the chain falls through to a
    configured fallback; the `TranscribeTimeoutError` wall-clock guard is left
    to propagate (hard abort).
    """

    name = "vast"

    def __init__(
        self,
        settings_obj: Settings | None = None,
        *,
        on_instance_created=None,
        on_destroy_failed=None,
        on_destroy_succeeded=None,
        check_monthly_cap=None,
    ) -> None:
        self._settings = settings_obj or default_settings
        self._on_instance_created = on_instance_created
        self._on_destroy_failed = on_destroy_failed
        self._on_destroy_succeeded = on_destroy_succeeded
        self._check_monthly_cap = check_monthly_cap

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        try:
            return whisper_client.transcribe(
                request.wav,
                title=request.title,
                source_url=request.source_url,
                model_size=request.model_size,
                compute_type=request.compute_type,
                language=request.language,
                beam_size=request.beam_size,
                on_instance_created=self._on_instance_created,
                on_destroy_failed=self._on_destroy_failed,
                on_destroy_succeeded=self._on_destroy_succeeded,
                check_monthly_cap=self._check_monthly_cap,
            )
        except TranscribeTimeoutError:
            # Whole-job wall-clock budget elapsed — do not chain to a slower
            # provider; let the worker fail the job with the original error.
            raise
        except WhisperError as exc:
            raise TranscribeProviderUnavailableError(
                reason="vast_unavailable", details=str(exc)
            ) from exc


class OpenAITranscribeProvider:
    """Hosted OpenAI Whisper API fallback.

    POSTs the wav to `${base_url}/audio/transcriptions` with bearer auth and
    `response_format=verbose_json`. Opt-in: an empty api key reports
    unavailable. Cost-capped: when a duration hint is present the per-job
    estimate (duration minutes × cost/minute) is checked against the cap
    before upload, and the realised estimate is surfaced via
    `transcribe_provider_spend_usd_total{provider="openai"}`.

    429 → usage_limit; 5xx → unavailable; httpx timeout → timeout; cost-cap
    rejection → generic error (advances the chain without tripping the
    breaker, since it is a per-job budget decision, not an outage).
    """

    name = "openai"

    def __init__(self, settings_obj: Settings | None = None) -> None:
        self._settings = settings_obj or default_settings

    def _estimate_cost(self, duration_seconds: float | None) -> float | None:
        if duration_seconds is None or duration_seconds <= 0:
            return None
        minutes = duration_seconds / 60.0
        return minutes * float(self._settings.openai_transcribe_cost_per_minute_usd)

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        s = self._settings
        if not s.openai_transcribe_api_key.strip():
            raise TranscribeProviderUnavailableError(
                reason="openai_no_api_key",
                details="SCRIBE_OPENAI_TRANSCRIBE_API_KEY not configured",
            )

        cap = float(s.openai_transcribe_max_job_cost_usd)
        estimate = self._estimate_cost(request.duration_seconds)
        if estimate is not None and cap > 0 and estimate > cap:
            raise TranscribeProviderError(
                reason="openai_cost_cap",
                details=(
                    f"estimated ${estimate:.4f} for {request.duration_seconds}s "
                    f"exceeds per-job cap ${cap:.2f}"
                ),
            )

        url = f"{s.openai_transcribe_base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {s.openai_transcribe_api_key}"}
        data: dict[str, str] = {
            "model": s.openai_transcribe_model,
            "response_format": "verbose_json",
        }
        if request.language and request.language != "auto":
            data["language"] = request.language
        try:
            files = {"file": (request.wav.name, request.wav.read_bytes(), "audio/wav")}
            resp = httpx.post(
                url,
                headers=headers,
                data=data,
                files=files,
                timeout=s.openai_transcribe_timeout_secs,
            )
        except httpx.TimeoutException as exc:
            raise TranscribeProviderTimeoutError(
                reason="timeout",
                details=f"openai transcribe timed out after {s.openai_transcribe_timeout_secs}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise TranscribeProviderUnavailableError(
                reason="openai_transport_error", details=str(exc)
            ) from exc

        if resp.status_code == 429:
            raise TranscribeProviderUsageLimitError(
                reason="openai_usage_limit", details=resp.text[:400]
            )
        if 500 <= resp.status_code < 600:
            raise TranscribeProviderUnavailableError(
                reason="openai_5xx", details=f"{resp.status_code}: {resp.text[:400]}"
            )
        if resp.status_code >= 400:
            raise TranscribeProviderError(
                reason="openai_http_error",
                details=f"{resp.status_code}: {resp.text[:400]}",
            )

        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise TranscribeProviderError(
                reason="openai_bad_response", details=f"non-JSON body: {exc}"
            ) from exc

        transcript_text = str(payload.get("text") or "").strip()
        if not transcript_text:
            raise TranscribeProviderError(
                reason="empty_response", details="openai returned empty transcript"
            )
        detected_language = str(payload.get("language") or "unknown")
        raw_duration = payload.get("duration")
        duration = float(raw_duration) if isinstance(raw_duration, (int, float)) else (
            float(request.duration_seconds) if request.duration_seconds else None
        )
        backend = f"openai ({s.openai_transcribe_model})"
        cost = self._estimate_cost(duration) or 0.0

        return TranscribeResult(
            transcript_md=_render_transcript_md(
                title=request.title,
                source_url=request.source_url,
                source_audio="OpenAI hosted Whisper API",
                backend=backend,
                detected_language=detected_language,
                language_probability=None,
                duration=duration,
                transcript_text=transcript_text,
            ),
            detected_language=detected_language,
            duration_seconds=duration,
            backend=backend,
            vast_instance_id=0,
            vast_cost=cost,
            provider=self.name,
        )


class LocalWhisperProvider:
    """CPU `faster-whisper` fallback — slow but always available.

    `faster_whisper` is an optional dependency; if it is not importable the
    provider reports unavailable and the chain advances. Produces the same
    transcript markdown shape as the Vast remote script. Cost is zero (local
    compute), so it does not contribute to any spend cap.
    """

    name = "local-whisper"

    def __init__(self, settings_obj: Settings | None = None) -> None:
        self._settings = settings_obj or default_settings

    def transcribe(self, request: TranscribeRequest) -> TranscribeResult:
        s = self._settings
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscribeProviderUnavailableError(
                reason="faster_whisper_missing",
                details="faster-whisper is not installed in this image",
            ) from exc

        language = None if request.language == "auto" else request.language
        try:
            model = WhisperModel(
                s.local_whisper_model_size,
                device="cpu",
                compute_type=s.local_whisper_compute_type,
            )
            segments, info = model.transcribe(
                str(request.wav),
                language=language,
                beam_size=request.beam_size,
                vad_filter=True,
            )
            collected = list(segments)
        except Exception as exc:  # noqa: BLE001 — surface any backend error as provider-unavailable
            raise TranscribeProviderUnavailableError(
                reason="faster_whisper_error", details=f"{type(exc).__name__}: {exc}"
            ) from exc

        transcript_text = " ".join(
            seg.text.strip() for seg in collected if seg.text.strip()
        ).strip()
        if not transcript_text:
            raise TranscribeProviderError(
                reason="empty_response", details="local faster-whisper produced no text"
            )
        duration = max((seg.end for seg in collected), default=None)
        detected_language = getattr(info, "language", None) or "unknown"
        language_probability = getattr(info, "language_probability", None)
        backend = (
            f"faster-whisper ({s.local_whisper_model_size}, "
            f"{s.local_whisper_compute_type}, cpu)"
        )
        return TranscribeResult(
            transcript_md=_render_transcript_md(
                title=request.title,
                source_url=request.source_url,
                source_audio="Local CPU faster-whisper",
                backend=backend,
                detected_language=detected_language,
                language_probability=language_probability,
                duration=duration,
                transcript_text=transcript_text,
            ),
            detected_language=detected_language,
            duration_seconds=duration,
            backend=backend,
            vast_instance_id=0,
            vast_cost=0.0,
            provider=self.name,
        )


PROVIDER_REGISTRY: dict[str, type[TranscribeProvider]] = {
    "vast": VastProvider,
    "openai": OpenAITranscribeProvider,
    "local-whisper": LocalWhisperProvider,
}


def build_provider_chain(
    settings_obj: Settings | None = None,
    *,
    on_instance_created=None,
    on_destroy_failed=None,
    on_destroy_succeeded=None,
    check_monthly_cap=None,
) -> list[TranscribeProvider]:
    """Instantiate the configured provider chain.

    Reads provider names from `settings.transcribe_providers`. Unknown names
    raise `ValueError` rather than being silently dropped — a typo in env
    should be surfaced loudly at process start, not buried in a fallback path.
    The Vast lifecycle callbacks are forwarded only to `VastProvider`; other
    providers ignore them.
    """
    s = settings_obj or default_settings
    names: list[str] = list(s.transcribe_providers) if s.transcribe_providers else []
    unknown = [n for n in names if n not in PROVIDER_REGISTRY]
    if unknown:
        raise ValueError(
            "unknown transcribe providers in SCRIBE_TRANSCRIBE_PROVIDERS: "
            + ", ".join(unknown)
            + f". Known providers: {sorted(PROVIDER_REGISTRY)}"
        )
    chain: list[TranscribeProvider] = []
    for name in names:
        if name == "vast":
            chain.append(
                VastProvider(
                    s,
                    on_instance_created=on_instance_created,
                    on_destroy_failed=on_destroy_failed,
                    on_destroy_succeeded=on_destroy_succeeded,
                    check_monthly_cap=check_monthly_cap,
                )
            )
        else:
            chain.append(PROVIDER_REGISTRY[name](s))
    return chain
