"""Per-provider summary shape validator + canonicalisation tests.

Golden samples in `tests/fixtures/summary_outputs/` represent the actual
shapes different LLM backends emit. The integration test exercises a fake
`ClaudeProvider` through the chain helper to prove that a provider whose
raw output needs repair still resolves to a clean `SummaryResult` without
the chain falling through.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scribe.pipeline.summary_providers import (
    ProviderError,
    SummaryResult,
    summarize_with_chain,
)
from scribe.pipeline.summary_validator import validate_and_canonicalize

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "summary_outputs"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


# ---------- happy path: codex canonical ---------------------------------------


def test_codex_canonical_validates_unchanged() -> None:
    raw = _load("codex_canonical.md")
    result = validate_and_canonicalize(raw)

    assert result.tags == ["llm", "local-ai", "performance"]
    assert result.short_description == (
        "Автор разбирает экономику локальных LLM и показывает, "
        "где self-hosting реально экономит."
    )
    # Canonical input must round-trip body verbatim (modulo trailing newline).
    assert result.summary_md.strip() == raw.strip()
    assert "## TL;DR" in result.summary_md
    # No accidental H1 introduced.
    assert "\n# " not in result.summary_md


# ---------- claude: extra prose before/after frontmatter ----------------------


def test_claude_with_extras_repairs_and_keeps_body() -> None:
    raw = _load("claude_with_extras.md")
    result = validate_and_canonicalize(raw)

    assert result.tags == ["llm", "consumer-gpu", "benchmarks"]
    assert "потребительских GPU" in (result.short_description or "")
    # Body content survives canonicalisation.
    assert "Inference на потребительских GPU" in result.summary_md
    assert "VRAM bottleneck" in result.summary_md
    # Output starts with canonical frontmatter, not the chatty preamble.
    assert result.summary_md.startswith("---\n")
    assert "Sure! Here's the summary you asked for" not in result.summary_md


# ---------- freellmapi: # H1 headers downshifted to ## H2 --------------------


def test_freellmapi_wrong_headers_downshift_to_h2() -> None:
    raw = _load("freellmapi_wrong_headers.md")
    result = validate_and_canonicalize(raw)

    assert result.tags == ["transformers", "architecture", "deep-learning"]
    # Every former H1 became H2.
    assert "## Архитектура трансформеров" in result.summary_md
    assert "## TL;DR" in result.summary_md
    assert "## Основная идея" in result.summary_md
    # No `#` H1 headers remain in the body.
    for line in result.summary_md.splitlines():
        assert not (line.startswith("# ") and not line.startswith("## "))


# ---------- freellmapi: no frontmatter at all → ProviderError ----------------


def test_freellmapi_no_frontmatter_raises_provider_error() -> None:
    raw = _load("freellmapi_no_frontmatter.md")
    with pytest.raises(ProviderError) as exc_info:
        validate_and_canonicalize(raw)
    assert exc_info.value.reason == "shape_invalid"
    assert "frontmatter" in exc_info.value.details.lower()


# ---------- freellmapi: tags as string → split into list ---------------------


def test_freellmapi_tags_as_string_parsed_to_list() -> None:
    raw = _load("freellmapi_tags_as_string.md")
    result = validate_and_canonicalize(raw)

    assert result.tags == ["ai", "python", "tooling"]
    # Rebuilt frontmatter emits tags as a YAML list.
    assert "tags: [ai, python, tooling]" in result.summary_md


# ---------- validator: additional unit cases ---------------------------------


def test_empty_input_raises_provider_error() -> None:
    with pytest.raises(ProviderError) as exc_info:
        validate_and_canonicalize("")
    assert exc_info.value.reason == "shape_invalid"


def test_frontmatter_with_no_tags_raises() -> None:
    md = '---\nshort_description: "x"\n---\n\n## Body\n\nText.\n'
    with pytest.raises(ProviderError) as exc_info:
        validate_and_canonicalize(md)
    assert "tags" in exc_info.value.details.lower()


def test_missing_short_description_falls_back_to_first_body_line() -> None:
    md = (
        "---\n"
        "tags: [ai]\n"
        "---\n"
        "\n"
        "## Topic\n"
        "\n"
        "First body sentence becomes the short description.\n"
        "\n"
        "Second paragraph.\n"
    )
    result = validate_and_canonicalize(md)
    assert result.short_description == (
        "First body sentence becomes the short description."
    )


def test_short_description_fallback_truncates_to_200_chars() -> None:
    long_line = "x" * 500
    md = f"---\ntags: [ai]\n---\n\n{long_line}\n"
    result = validate_and_canonicalize(md)
    assert result.short_description is not None
    assert len(result.short_description) == 200
    assert result.short_description == "x" * 200


def test_outer_code_fence_is_stripped() -> None:
    md = (
        "```markdown\n"
        "---\n"
        "tags: [ai]\n"
        'short_description: "wrapped in fences"\n'
        "---\n"
        "\n"
        "## Body\n"
        "\n"
        "Content.\n"
        "```\n"
    )
    result = validate_and_canonicalize(md)
    assert result.tags == ["ai"]
    assert result.short_description == "wrapped in fences"
    assert result.summary_md.startswith("---\n")
    assert "```" not in result.summary_md


def test_repair_finds_frontmatter_after_chatty_preamble() -> None:
    md = (
        "Of course! Here you go:\n"
        "\n"
        "---\n"
        "tags: [ai, ml]\n"
        'short_description: "Recovered from chatty prefix"\n'
        "---\n"
        "\n"
        "## TL;DR\n"
        "\n"
        "Body content.\n"
    )
    result = validate_and_canonicalize(md)
    assert result.tags == ["ai", "ml"]
    assert result.short_description == "Recovered from chatty prefix"


def test_provider_error_carries_reason_and_details() -> None:
    err = ProviderError(reason="shape_invalid", details="missing tags")
    assert err.reason == "shape_invalid"
    assert err.details == "missing tags"
    assert "shape_invalid" in str(err)
    assert "missing tags" in str(err)


# ---------- integration: provider chain --------------------------------------


class _FakeProvider:
    """Minimal `SummaryProvider` for chain integration tests."""

    def __init__(self, name: str, payload: str | ProviderError) -> None:
        self.name = name
        self._payload = payload

    def summarize(self, prompt: str) -> SummaryResult:  # noqa: ARG002
        if isinstance(self._payload, ProviderError):
            raise self._payload
        return validate_and_canonicalize(self._payload)


def test_chain_returns_first_successful_provider() -> None:
    raw = _load("claude_with_extras.md")
    claude = _FakeProvider("claude", raw)
    codex = _FakeProvider("codex", _load("codex_canonical.md"))

    result = summarize_with_chain([claude, codex], prompt="<ignored>")

    # First provider succeeded; we never fell through to codex.
    assert result.tags == ["llm", "consumer-gpu", "benchmarks"]
    assert "Inference на потребительских GPU" in result.summary_md


def test_chain_falls_through_on_provider_error() -> None:
    failing = _FakeProvider("freellmapi_garbage", _load("freellmapi_no_frontmatter.md"))
    healthy = _FakeProvider("codex", _load("codex_canonical.md"))

    result = summarize_with_chain([failing, healthy], prompt="<ignored>")

    # Validator raised ProviderError on the freellmapi response, chain
    # advanced to codex, which returned a clean SummaryResult.
    assert result.tags == ["llm", "local-ai", "performance"]


def test_chain_exhausted_raises_provider_error() -> None:
    a = _FakeProvider("a", _load("freellmapi_no_frontmatter.md"))
    b = _FakeProvider("b", ProviderError(reason="shape_invalid", details="b failed"))

    with pytest.raises(ProviderError) as exc_info:
        summarize_with_chain([a, b], prompt="<ignored>")
    assert exc_info.value.reason == "chain_exhausted"


def test_chain_with_no_providers_raises() -> None:
    with pytest.raises(ProviderError) as exc_info:
        summarize_with_chain([], prompt="<ignored>")
    assert exc_info.value.reason == "no_providers"


def test_chain_propagates_non_provider_error() -> None:
    class _BoomProvider:
        name = "boom"

        def summarize(self, prompt: str) -> SummaryResult:  # noqa: ARG002
            raise RuntimeError("auth failure — not a shape problem")

    with pytest.raises(RuntimeError, match="auth failure"):
        summarize_with_chain([_BoomProvider()], prompt="<ignored>")


def test_strips_unclosed_leading_fence_before_frontmatter():
    """Provider opens a ``` fence before the frontmatter but never closes
    it on the final line. The leading fence must still be stripped so the
    frontmatter is not doubled downstream (issue #304)."""
    fence = "```"
    md = (
        f"{fence}\n"
        "---\n"
        "type: summary\n"
        "tags: [alpha, beta]\n"
        'short_description: "A concise summary."\n'
        "---\n\n"
        "## Section\n\nBody line one."
    )
    result = validate_and_canonicalize(md)
    assert "```" not in result.summary_md
    assert result.tags == ["alpha", "beta"]
    # exactly one frontmatter block (one opening + one closing fence)
    assert result.summary_md.count("---") == 2
    assert result.summary_md.startswith("---\ntype: summary")


# ---------- size cap (#349) ---------------------------------------------------


def _valid_summary_md(body_chars: int) -> str:
    body = "x" * body_chars
    return (
        "---\n"
        "tags: [ai]\n"
        'short_description: "ok"\n'
        "---\n\n"
        "## Body\n\n"
        f"{body}\n"
    )


def test_oversized_summary_rejected_as_too_large() -> None:
    md = _valid_summary_md(body_chars=2000)
    with pytest.raises(ProviderError) as exc_info:
        validate_and_canonicalize(md, max_chars=1000)
    assert exc_info.value.reason == "summary_too_large"
    assert "exceeds cap" in exc_info.value.details


def test_normal_summary_unaffected_by_cap() -> None:
    md = _valid_summary_md(body_chars=500)
    result = validate_and_canonicalize(md, max_chars=1000)
    assert result.tags == ["ai"]
    assert result.short_description == "ok"
    assert "## Body" in result.summary_md


def test_cap_at_boundary_accepted() -> None:
    # Exactly at the cap (== cap) is accepted; one char over is rejected.
    md = _valid_summary_md(body_chars=10)
    assert validate_and_canonicalize(md, max_chars=len(md)).tags == ["ai"]
    with pytest.raises(ProviderError, match="summary_too_large"):
        validate_and_canonicalize(md, max_chars=len(md) - 1)


def test_disabled_cap_allows_oversized() -> None:
    md = _valid_summary_md(body_chars=5000)
    # max_chars=0 disables the cap entirely.
    result = validate_and_canonicalize(md, max_chars=0)
    assert result.tags == ["ai"]
