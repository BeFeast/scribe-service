"""Tests for map-reduce summarization of oversized transcripts (#382).

Covers the splitter, the `map_reduce_summarize` helper (map + reduce call
pattern, truncation degradation, error propagation, metrics) and the
`summarize_with_chain` integration that activates map-reduce when the built
prompt exceeds the configured threshold — including an end-to-end simulation
of the freellmapi 413 PayloadTooLargeError that motivated the change.
"""
from __future__ import annotations

import json

import httpx
import pytest

from scribe.config import Settings
from scribe.obs import metrics
from scribe.pipeline import summary_providers
from scribe.pipeline.summary_map_reduce import map_reduce_summarize, split_transcript
from scribe.pipeline.summary_providers import (
    FreeLLMAPIProvider,
    ProviderError,
    SummaryResult,
    summarize_with_chain,
)

_OK_MARKDOWN = (
    "---\n"
    "tags: [ai, infra]\n"
    'short_description: "first sentence about the topic"\n'
    "---\n\n"
    "## TL;DR\n\nBody content stays.\n"
)


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    summary_providers._reset_breakers_for_test()
    yield
    summary_providers._reset_breakers_for_test()


def _counter_value(provider: str, result: str) -> float:
    return metrics.summary_map_reduce_total.labels(provider=provider, result=result)._value.get()


# ---------- split_transcript --------------------------------------------------


def test_split_short_text_is_single_chunk() -> None:
    assert split_transcript("hello world", chunk_chars=1000, overlap_chars=100) == [
        "hello world"
    ]


def test_split_empty_text_returns_empty_list() -> None:
    assert split_transcript("   ", chunk_chars=1000, overlap_chars=100) == []


def test_split_disabled_when_chunk_chars_non_positive() -> None:
    text = "a" * 5000
    assert split_transcript(text, chunk_chars=0, overlap_chars=0) == [text]


def test_split_respects_chunk_size_and_covers_text() -> None:
    paragraphs = [f"Paragraph number {i} has some sentences. More words here." for i in range(60)]
    text = "\n\n".join(paragraphs)
    chunks = split_transcript(text, chunk_chars=500, overlap_chars=0)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    # Every paragraph's distinctive marker survives somewhere in the chunks.
    joined = "\n".join(chunks)
    for i in range(60):
        assert f"Paragraph number {i} " in joined


def test_split_overlap_repeats_boundary_sentence() -> None:
    sentences = [f"Sentence {i} carries unique token tok{i}." for i in range(40)]
    text = " ".join(sentences)
    no_overlap = split_transcript(text, chunk_chars=300, overlap_chars=0)
    with_overlap = split_transcript(text, chunk_chars=300, overlap_chars=120)
    assert len(with_overlap) >= 2
    # Overlap repeats trailing context, so the total characters grow.
    assert sum(len(c) for c in with_overlap) > sum(len(c) for c in no_overlap)


# ---------- map_reduce_summarize ---------------------------------------------


class _ScriptedCompleter:
    """Returns `map_reply` for map prompts, `reduce_reply` for the reduce pass,
    recording every prompt it receives."""

    def __init__(self, name: str, *, map_reply: str, reduce_reply: str) -> None:
        self.name = name
        self.map_reply = map_reply
        self.reduce_reply = reduce_reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Transcript part" in prompt:
            return self.map_reply
        return self.reduce_reply

    @property
    def map_calls(self) -> int:
        return sum(1 for p in self.prompts if "Transcript part" in p)

    @property
    def reduce_calls(self) -> int:
        return sum(1 for p in self.prompts if "Transcript part" not in p)


def _settings(**overrides: int) -> Settings:
    s = Settings()
    s.summary_map_reduce_chars = overrides.get("chars", 4000)
    s.summary_map_reduce_chunk_chars = overrides.get("chunk", 1000)
    s.summary_map_reduce_overlap_chars = overrides.get("overlap", 0)
    return s


def test_map_reduce_calls_provider_per_chunk_plus_reduce() -> None:
    transcript = "\n\n".join(f"Paragraph {i} with content." for i in range(200))
    provider = _ScriptedCompleter(
        "freellmapi", map_reply="- key point", reduce_reply=_OK_MARKDOWN
    )

    result = map_reduce_summarize(
        provider,
        instructions="INSTRUCTIONS",
        transcript=transcript,
        settings=_settings(chars=4000, chunk=1000),
    )

    assert isinstance(result, SummaryResult)
    assert result.tags == ["ai", "infra"]
    assert provider.map_calls >= 2  # multiple map passes
    assert provider.reduce_calls == 1  # exactly one final reduce
    # Reduce prompt reuses the original instructions verbatim.
    reduce_prompt = next(p for p in provider.prompts if "Transcript part" not in p)
    assert reduce_prompt.startswith("INSTRUCTIONS")


def test_map_reduce_result_passes_validate_and_canonicalize() -> None:
    transcript = "\n\n".join(f"Block {i}." for i in range(400))
    provider = _ScriptedCompleter(
        "freellmapi", map_reply="- a\n- b", reduce_reply=_OK_MARKDOWN
    )
    result = map_reduce_summarize(
        provider,
        instructions="INSTRUCTIONS",
        transcript=transcript,
        settings=_settings(),
    )
    # Canonical shape: frontmatter + level-2 headers.
    assert result.summary_md.startswith("---\n")
    assert "## TL;DR" in result.summary_md


def test_map_reduce_skips_empty_partials_but_keeps_reduce() -> None:
    transcript = "\n\n".join(f"Para {i} text." for i in range(200))
    provider = _ScriptedCompleter(
        "freellmapi", map_reply="   ", reduce_reply=_OK_MARKDOWN
    )
    with pytest.raises(ProviderError) as exc:
        map_reduce_summarize(
            provider,
            instructions="INSTRUCTIONS",
            transcript=transcript,
            settings=_settings(),
        )
    assert exc.value.reason == "empty_response"


def test_map_reduce_truncates_when_partials_overflow_threshold() -> None:
    transcript = "\n\n".join(f"Para {i} text body." for i in range(400))
    # Each partial is large, so the combined reduce input overflows the
    # threshold and must be truncated with a visible marker.
    provider = _ScriptedCompleter(
        "freellmapi", map_reply="X" * 800, reduce_reply=_OK_MARKDOWN
    )
    result = map_reduce_summarize(
        provider,
        instructions="INSTRUCTIONS",
        transcript=transcript,
        settings=_settings(chars=3000, chunk=1000),
    )
    assert "> [truncated: transcript exceeded 3000 chars]" in result.summary_md
    # Marker lands in the body, after the frontmatter block.
    body = result.summary_md.split("---\n", 2)[-1]
    assert body.lstrip().startswith("> [truncated:")
    assert _counter_value("freellmapi", "truncated") >= 1


def test_map_reduce_propagates_provider_error_on_map_failure() -> None:
    transcript = "\n\n".join(f"Para {i}." for i in range(200))

    class _FailingMap:
        name = "freellmapi"

        def complete(self, prompt: str) -> str:
            raise ProviderError(reason="freellmapi_http_error", details="413")

    with pytest.raises(ProviderError) as exc:
        map_reduce_summarize(
            _FailingMap(),
            instructions="INSTRUCTIONS",
            transcript=transcript,
            settings=_settings(),
        )
    assert exc.value.reason == "freellmapi_http_error"
    assert _counter_value("freellmapi", "failed") >= 1


# ---------- summarize_with_chain integration ----------------------------------


def _enable_map_reduce(monkeypatch: pytest.MonkeyPatch, **overrides: int) -> None:
    s = summary_providers.default_settings
    monkeypatch.setattr(s, "summary_map_reduce_chars", overrides.get("chars", 2000))
    monkeypatch.setattr(s, "summary_map_reduce_chunk_chars", overrides.get("chunk", 1000))
    monkeypatch.setattr(s, "summary_map_reduce_overlap_chars", overrides.get("overlap", 0))


class _ChainCompleter:
    """Chain-facing provider exposing both summarize (single pass) and complete
    (map-reduce). Records which path the chain took."""

    def __init__(self, name: str, *, reply: str = _OK_MARKDOWN) -> None:
        self.name = name
        self.reply = reply
        self.summarize_calls = 0
        self.complete_calls = 0

    def complete(self, prompt: str) -> str:
        self.complete_calls += 1
        return self.reply

    def summarize(self, prompt: str) -> SummaryResult:
        self.summarize_calls += 1
        return summary_providers.validate_and_canonicalize(self.reply)


def test_chain_short_prompt_stays_single_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_map_reduce(monkeypatch, chars=2000)
    provider = _ChainCompleter("freellmapi")
    summarize_with_chain(
        [provider],
        "short prompt",
        instructions="INSTRUCTIONS",
        transcript="short transcript",
    )
    assert provider.summarize_calls == 1
    assert provider.complete_calls == 0  # no map-reduce


def test_chain_without_transcript_never_map_reduces(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_map_reduce(monkeypatch, chars=10)
    provider = _ChainCompleter("freellmapi")
    # Long prompt but no transcript supplied (legacy callers / tests).
    summarize_with_chain([provider], "x" * 5000)
    assert provider.summarize_calls == 1
    assert provider.complete_calls == 0


def test_chain_threshold_zero_disables_map_reduce(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_map_reduce(monkeypatch, chars=0)
    provider = _ChainCompleter("freellmapi")
    transcript = "word " * 2000
    prompt = "INSTRUCTIONS" + transcript
    summarize_with_chain(
        [provider], prompt, instructions="INSTRUCTIONS", transcript=transcript
    )
    assert provider.summarize_calls == 1
    assert provider.complete_calls == 0


def test_chain_long_prompt_uses_map_reduce(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_map_reduce(monkeypatch, chars=2000, chunk=1000)
    provider = _ChainCompleter("freellmapi")
    transcript = "\n\n".join(f"Paragraph {i} body content." for i in range(200))
    prompt = "INSTRUCTIONS" + transcript
    assert len(prompt) > 2000

    result = summarize_with_chain(
        [provider], prompt, instructions="INSTRUCTIONS", transcript=transcript
    )
    assert isinstance(result, SummaryResult)
    assert provider.summarize_calls == 0  # never single-pass
    assert provider.complete_calls >= 2  # map chunks + reduce


def test_chain_falls_through_to_next_provider_when_map_reduce_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_map_reduce(monkeypatch, chars=2000, chunk=1000)

    class _FailingChainProvider:
        name = "codex"

        def __init__(self) -> None:
            self.complete_calls = 0

        def complete(self, prompt: str) -> str:
            self.complete_calls += 1
            raise ProviderError(reason="codex_error", details="boom")

        def summarize(self, prompt: str) -> SummaryResult:  # pragma: no cover
            raise AssertionError("single-pass must not run for long prompts")

    failing = _FailingChainProvider()
    healthy = _ChainCompleter("freellmapi")
    transcript = "\n\n".join(f"Paragraph {i} body." for i in range(200))
    prompt = "INSTRUCTIONS" + transcript

    result = summarize_with_chain(
        [failing, healthy], prompt, instructions="INSTRUCTIONS", transcript=transcript
    )
    assert isinstance(result, SummaryResult)
    assert failing.complete_calls >= 1  # attempted map
    assert healthy.complete_calls >= 2  # recovered via its own map-reduce


# ---------- end-to-end: freellmapi 413 simulation -----------------------------


def test_freellmapi_413_long_transcript_produces_summary_via_map_reduce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance #8: a transcript that exceeds the freellmapi payload limit in
    a single body yields a valid summary via map-reduce instead of a 413."""
    monkeypatch.setattr(summary_providers.default_settings, "freellmapi_base_url", "http://proxy/v1")
    monkeypatch.setattr(summary_providers.default_settings, "freellmapi_api_key", "test-key")
    monkeypatch.setattr(summary_providers.default_settings, "freellmapi_model", "gpt-4o-mini")
    monkeypatch.setattr(summary_providers.default_settings, "freellmapi_timeout_secs", 30)
    _enable_map_reduce(monkeypatch, chars=2000, chunk=1000)

    payload_limit = 3000  # bytes of request body the fake server accepts
    sent_lengths: list[int] = []

    def fake_post(url, *, content, headers, timeout):  # noqa: ARG001
        sent_lengths.append(len(content))
        if len(content) > payload_limit:
            return httpx.Response(
                413,
                text='{"error":{"message":"request entity too large",'
                '"type":"PayloadTooLargeError"}}',
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(summary_providers.httpx, "post", fake_post)

    transcript = "\n\n".join(
        f"Paragraph {i} discusses an interesting topic in detail." for i in range(300)
    )
    prompt = "INSTRUCTIONS" + transcript
    # The single-pass prompt alone would exceed the payload limit.
    assert len(json.dumps({"content": prompt})) > payload_limit

    result = summarize_with_chain(
        [FreeLLMAPIProvider()],
        prompt,
        instructions="INSTRUCTIONS",
        transcript=transcript,
    )
    assert isinstance(result, SummaryResult)
    assert result.tags == ["ai", "infra"]
    # No single request ever exceeded the payload limit → no 413 path hit.
    assert sent_lengths and all(length <= payload_limit for length in sent_lengths)
    assert len(sent_lengths) >= 2  # several map calls + reduce
