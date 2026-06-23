"""Tests for the concrete summary providers and the summarize() fallback path.

The chain primitive + breaker behaviour are exercised in
`test_summary_circuit_breaker.py`; this module focuses on each provider's
stderr/HTTP classification and the high-level `summarize()` wrapper that
translates `ProviderError(chain_exhausted)` into `SummarizeError` /
`CodexTokenRevokedError`.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from scribe.obs import metrics
from scribe.pipeline import summarizer, summary_providers
from scribe.pipeline.summary_providers import (
    ClaudeProvider,
    CodexProvider,
    FreeLLMAPIProvider,
    OllamaCloudProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUsageLimitError,
    SummaryResult,
    build_provider_chain,
    parse_provider_entry,
    summarize_with_chain,
)


def _hist_count(hist: Any) -> float:
    """Return the `_count` sample of an unlabelled Histogram via collect()."""
    metric = next(iter(hist.collect()), None)
    if metric is None:
        return 0.0
    for sample in metric.samples:
        if sample.name.endswith("_count"):
            return sample.value
    return 0.0


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    summary_providers._reset_breakers_for_test()
    yield
    summary_providers._reset_breakers_for_test()


_OK_MARKDOWN = (
    "---\n"
    "tags: [ai, infra]\n"
    'short_description: "first sentence about the topic"\n'
    "---\n\n"
    "## TL;DR\n\nBody content stays.\n"
)


# ---------- CodexProvider -----------------------------------------------------


def _patch_codex_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")
    monkeypatch.setattr(summarizer.settings, "codex_reasoning", "low")
    monkeypatch.setattr(summarizer.settings, "codex_timeout_secs", 600)


def test_codex_provider_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(_OK_MARKDOWN, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    provider = CodexProvider()
    result = provider.summarize("prompt")
    assert isinstance(result, SummaryResult)
    assert "ai" in result.tags
    assert provider.last_token_revoked_stderr is None


@pytest.mark.parametrize(
    "stderr",
    [
        "you have hit the daily usage limit",
        "OpenAI: rate limit exceeded, try again later",
        "QUOTA EXCEEDED for this account",
    ],
)
def test_codex_provider_usage_limit_stderr_maps_to_usage_limit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr=stderr)

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderUsageLimitError) as exc:
        CodexProvider().summarize("prompt")
    assert exc.value.reason == "codex_usage_limit"


@pytest.mark.parametrize(
    "stderr",
    [
        'invalid_request_error", "code": "refresh_token_reused"',
        "Encountered invalidated oauth token for user, failing request",
        'status_code=401, message="token_revoked"',
        "ERROR: Please log out and sign in again",
    ],
)
def test_codex_provider_token_revoked_stderr_maps_to_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr)

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    provider = CodexProvider()
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.summarize("prompt")
    assert exc.value.reason == "codex_token_revoked"
    assert provider.last_token_revoked_stderr is not None
    assert stderr in provider.last_token_revoked_stderr


def test_codex_provider_timeout_maps_to_provider_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderTimeoutError):
        CodexProvider().summarize("prompt")


def test_codex_provider_generic_error_when_nonzero_without_known_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom: unhandled")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderError) as exc:
        CodexProvider().summarize("prompt")
    assert exc.value.reason == "codex_error"
    assert not isinstance(exc.value, ProviderUsageLimitError)
    assert not isinstance(exc.value, ProviderUnavailableError)


# ---------- CodexProvider lock-wait bounding (issue #352) ---------------------


def test_codex_provider_records_lock_wait_metric_on_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uncontended path still publishes the lock-wait histogram (≈0s) so
    contention is observable from a stable baseline."""
    _patch_codex_settings(monkeypatch, tmp_path)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(_OK_MARKDOWN, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    before = _hist_count(metrics.codex_lock_wait_seconds)
    CodexProvider().summarize("prompt")
    assert _hist_count(metrics.codex_lock_wait_seconds) == before + 1


def test_codex_provider_lock_held_times_out_without_running_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When another process holds the codex lock, acquisition is bounded by
    codex_lock_wait_timeout_secs: the provider raises a (non-trip-relevant)
    ProviderError instead of blocking for the whole codex timeout, and never
    launches codex exec."""
    _patch_codex_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(summarizer.settings, "codex_lock_wait_timeout_secs", 0.2)

    def fail_run(*args: Any, **kwargs: Any):  # noqa: ARG001
        raise AssertionError("codex exec must not run while the lock is held")

    monkeypatch.setattr(summary_providers.subprocess, "run", fail_run)

    lock_path = tmp_path / "codex.lock"
    holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        before = _hist_count(metrics.codex_lock_wait_seconds)
        started = time.monotonic()
        with pytest.raises(ProviderError) as exc:
            CodexProvider().summarize("prompt")
        elapsed = time.monotonic() - started
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)

    assert exc.value.reason == "codex_lock_timeout"
    # Not a usage-limit / unavailable / timeout error, so it does not trip the
    # codex circuit breaker — codex is healthy, just busy.
    assert not isinstance(
        exc.value,
        (ProviderUsageLimitError, ProviderUnavailableError, ProviderTimeoutError),
    )
    # Bounded by the wait timeout, nowhere near codex_timeout_secs (600).
    assert elapsed < 5.0
    assert _hist_count(metrics.codex_lock_wait_seconds) == before + 1


def test_concurrent_summary_falls_through_codex_lock_to_next_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2: while one worker holds the codex lock for its full exec,
    a second worker does not serialise on it — the bounded wait elapses and the
    fallback chain advances to a concurrent provider, so both jobs summarise."""
    _patch_codex_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(summarizer.settings, "codex_lock_wait_timeout_secs", 0.2)

    def fail_run(*args: Any, **kwargs: Any):  # noqa: ARG001
        raise AssertionError("codex exec must not run while the lock is held")

    monkeypatch.setattr(summary_providers.subprocess, "run", fail_run)

    free = _ScriptedProvider("freellmapi", [_ok_summary()])
    chain = [CodexProvider(), free]

    lock_path = tmp_path / "codex.lock"
    holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        result = summarize_with_chain(chain, "prompt")
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)

    assert isinstance(result, SummaryResult)
    assert free.calls == 1
    # Codex did not trip its breaker on lock contention.
    assert summary_providers.get_breaker("codex").state == "closed"


# ---------- ClaudeProvider ---------------------------------------------------


def _patch_claude_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarizer.settings, "claude_bin", "claude")
    monkeypatch.setattr(summarizer.settings, "claude_model", "opus[1m]")
    monkeypatch.setattr(summarizer.settings, "claude_effort", "xhigh")
    monkeypatch.setattr(summarizer.settings, "claude_timeout_secs", 600)


def test_claude_provider_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claude_settings(monkeypatch)

    def fake_run(cmd, *, input, text, capture_output, timeout):  # noqa: ARG001
        assert "-p" in cmd
        assert input == "prompt-from-stdin"
        # prompt MUST NOT be in argv — it goes via stdin to avoid E2BIG.
        assert all(part != "prompt-from-stdin" for part in cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_OK_MARKDOWN, stderr="")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    result = ClaudeProvider().summarize("prompt-from-stdin")
    assert isinstance(result, SummaryResult)


@pytest.mark.parametrize(
    "stderr",
    [
        "Claude AI usage limit reached",
        "5-hour limit reached, try again later",
        "weekly limit reached",
        "OpenAI-style rate limit exceeded",
    ],
)
def test_claude_provider_usage_limit_stderr_maps_to_usage_limit_error(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    _patch_claude_settings(monkeypatch)

    def fake_run(cmd, *, input, text, capture_output, timeout):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr=stderr)

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderUsageLimitError):
        ClaudeProvider().summarize("prompt")


def test_claude_provider_missing_binary_maps_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_claude_settings(monkeypatch)

    def fake_run(*args: Any, **kwargs: Any):  # noqa: ARG001
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderUnavailableError) as exc:
        ClaudeProvider().summarize("prompt")
    assert exc.value.reason == "claude_missing"


def test_claude_provider_timeout_maps_to_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_claude_settings(monkeypatch)

    def fake_run(cmd, *, input, text, capture_output, timeout):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderTimeoutError):
        ClaudeProvider().summarize("prompt")


def test_claude_provider_unavailable_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_claude_settings(monkeypatch)

    def fake_run(cmd, *, input, text, capture_output, timeout):  # noqa: ARG001
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="please run `claude login` first"
        )

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderUnavailableError):
        ClaudeProvider().summarize("prompt")


def test_claude_provider_e2big_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kernel argv limit (E2BIG) must surface as ProviderError, not raw OSError,
    so the fallback chain advances rather than crashing."""
    import errno

    _patch_claude_settings(monkeypatch)

    def fake_run(*args: Any, **kwargs: Any):  # noqa: ARG001
        raise OSError(errno.E2BIG, "Argument list too long")

    monkeypatch.setattr(summary_providers.subprocess, "run", fake_run)
    with pytest.raises(ProviderError) as exc:
        ClaudeProvider().summarize("prompt")
    assert exc.value.reason == "claude_exec_failed"


# ---------- FreeLLMAPIProvider -----------------------------------------------


def _patch_freellmapi_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarizer.settings, "freellmapi_base_url", "http://proxy/v1")
    monkeypatch.setattr(summarizer.settings, "freellmapi_api_key", "test-key")
    monkeypatch.setattr(summarizer.settings, "freellmapi_model", "gpt-4o-mini")
    monkeypatch.setattr(summarizer.settings, "freellmapi_timeout_secs", 30)


def _stub_httpx_post(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    text: str = "",
    raise_exc: Exception | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url, *, content, headers, timeout):  # noqa: ARG001
        calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        if raise_exc is not None:
            raise raise_exc
        resp = httpx.Response(
            status_code,
            json=json_body if json_body is not None else None,
            text=text if json_body is None else None,
            request=httpx.Request("POST", url),
        )
        return resp

    monkeypatch.setattr(summary_providers.httpx, "post", fake_post)
    return calls


def test_freellmapi_provider_200_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    calls = _stub_httpx_post(
        monkeypatch,
        status_code=200,
        json_body={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
    )
    result = FreeLLMAPIProvider().summarize("hello")
    assert isinstance(result, SummaryResult)
    assert calls[0]["url"] == "http://proxy/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_freellmapi_provider_429_maps_to_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    _stub_httpx_post(monkeypatch, status_code=429, text="rate limit")
    with pytest.raises(ProviderUsageLimitError):
        FreeLLMAPIProvider().summarize("hello")


def test_freellmapi_provider_5xx_maps_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    _stub_httpx_post(monkeypatch, status_code=503, text="service unavailable")
    with pytest.raises(ProviderUnavailableError):
        FreeLLMAPIProvider().summarize("hello")


def test_freellmapi_provider_timeout_maps_to_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    _stub_httpx_post(
        monkeypatch,
        raise_exc=httpx.ReadTimeout("read timed out"),
    )
    with pytest.raises(ProviderTimeoutError):
        FreeLLMAPIProvider().summarize("hello")


def test_freellmapi_provider_other_4xx_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    _stub_httpx_post(monkeypatch, status_code=400, text="bad request")
    with pytest.raises(ProviderError) as exc:
        FreeLLMAPIProvider().summarize("hello")
    assert exc.value.reason == "freellmapi_http_error"


def test_freellmapi_provider_missing_api_key_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_freellmapi_settings(monkeypatch)
    monkeypatch.setattr(summarizer.settings, "freellmapi_api_key", "")
    with pytest.raises(ProviderUnavailableError) as exc:
        FreeLLMAPIProvider().summarize("hello")
    assert exc.value.reason == "freellmapi_no_api_key"


# ---------- OpenAICompatibleProvider (generic) -------------------------------


def test_openai_compatible_provider_posts_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic primitive POSTs to {base_url}/chat/completions with bearer
    auth and the configured model, returning the first choice's content."""
    calls = _stub_httpx_post(
        monkeypatch,
        status_code=200,
        json_body={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
    )
    provider = OpenAICompatibleProvider(
        name="ollama-cloud",
        base_url="http://ollama:11434/v1/",
        api_key="sk-test",
        model="glm-5.2",
        timeout=30,
    )
    result = provider.summarize("hello")
    assert isinstance(result, SummaryResult)
    assert calls[0]["url"] == "http://ollama:11434/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert json.loads(calls[0]["content"])["model"] == "glm-5.2"


def test_openai_compatible_provider_no_base_url_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance #20: a provider with no configured base URL is Unavailable
    (so the chain advances) rather than raising or making a bogus request."""
    posted = _stub_httpx_post(monkeypatch, status_code=200)
    provider = OpenAICompatibleProvider(
        name="ollama-cloud",
        base_url="",
        api_key="",
        model="glm-5.2",
        timeout=30,
        require_api_key=False,
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        provider.summarize("hello")
    assert exc.value.reason == "ollama-cloud_no_base_url"
    assert posted == []  # never hit the network


def test_openai_compatible_provider_omits_auth_header_when_keyless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A keyless backend (e.g. a local signed-in Ollama daemon) sends no
    Authorization header when require_api_key is False and no key is set."""
    calls = _stub_httpx_post(
        monkeypatch,
        status_code=200,
        json_body={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
    )
    provider = OpenAICompatibleProvider(
        name="ollama-cloud",
        base_url="http://ollama:11434/v1",
        api_key="",
        model="glm-5.2",
        timeout=30,
        require_api_key=False,
    )
    provider.summarize("hello")
    assert "Authorization" not in calls[0]["headers"]


def test_openai_compatible_provider_error_reasons_use_instance_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429/5xx/other-4xx error reasons are prefixed with the instance name so
    telemetry/breaker attribution is per-backend."""
    provider = OpenAICompatibleProvider(
        name="ollama-cloud",
        base_url="http://ollama:11434/v1",
        api_key="sk",
        model="glm-5.2",
        timeout=30,
    )
    _stub_httpx_post(monkeypatch, status_code=429, text="slow down")
    with pytest.raises(ProviderUsageLimitError) as exc:
        provider.summarize("x")
    assert exc.value.reason == "ollama-cloud_usage_limit"

    _stub_httpx_post(monkeypatch, status_code=503, text="down")
    with pytest.raises(ProviderUnavailableError) as exc2:
        provider.summarize("x")
    assert exc2.value.reason == "ollama-cloud_5xx"

    _stub_httpx_post(monkeypatch, status_code=400, text="bad")
    with pytest.raises(ProviderError) as exc3:
        provider.summarize("x")
    assert exc3.value.reason == "ollama-cloud_http_error"


def test_ollama_cloud_provider_unconfigured_base_url_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Out of the box (no SCRIBE_OLLAMA_BASE_URL) the ollama-cloud instance is
    Unavailable, so the default chain advances instead of crashing."""
    monkeypatch.setattr(summarizer.settings, "ollama_base_url", "")
    monkeypatch.setattr(summarizer.settings, "ollama_api_key", "")
    monkeypatch.setattr(summarizer.settings, "ollama_model", "glm-5.2")
    monkeypatch.setattr(summarizer.settings, "ollama_timeout_secs", 30)
    with pytest.raises(ProviderUnavailableError) as exc:
        OllamaCloudProvider().summarize("hello")
    assert exc.value.reason == "ollama-cloud_no_base_url"


def test_ollama_cloud_provider_keyless_daemon_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured base URL with no API key is fine for ollama-cloud (local
    signed-in daemon) — the call goes through without an Authorization header."""
    monkeypatch.setattr(summarizer.settings, "ollama_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(summarizer.settings, "ollama_api_key", "")
    monkeypatch.setattr(summarizer.settings, "ollama_model", "glm-5.2")
    monkeypatch.setattr(summarizer.settings, "ollama_timeout_secs", 30)
    calls = _stub_httpx_post(
        monkeypatch,
        status_code=200,
        json_body={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
    )
    result = OllamaCloudProvider(model="gemma4:31b").summarize("hello")
    assert isinstance(result, SummaryResult)
    assert "Authorization" not in calls[0]["headers"]
    assert json.loads(calls[0]["content"])["model"] == "gemma4:31b"


# ---------- parse_provider_entry ---------------------------------------------


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("codex", ("codex", None)),
        ("freellmapi", ("freellmapi", None)),
        ("ollama-cloud:glm-5.2", ("ollama-cloud", "glm-5.2")),
        # Model tag itself contains a ':' — split on the FIRST colon only.
        ("ollama-cloud:gemma4:31b", ("ollama-cloud", "gemma4:31b")),
        ("freellmapi:gemini-2.5-flash", ("freellmapi", "gemini-2.5-flash")),
        # Provider name is case-insensitive; model is kept verbatim.
        ("  Ollama-Cloud : MiniMax-M3  ", ("ollama-cloud", "MiniMax-M3")),
    ],
)
def test_parse_provider_entry(entry: str, expected: tuple[str, str | None]) -> None:
    assert parse_provider_entry(entry) == expected


# ---------- build_provider_chain ---------------------------------------------


def test_build_provider_chain_returns_configured_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["claude", "codex"])
    chain = build_provider_chain()
    assert [p.name for p in chain] == ["claude", "codex"]


def test_build_provider_chain_parses_per_provider_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance: a `provider:model` chain (including a model tag with a ':')
    builds in order with the right backend names and per-instance models."""
    monkeypatch.setattr(
        summarizer.settings,
        "summary_providers",
        ["ollama-cloud:glm-5.2", "ollama-cloud:gemma4:31b", "freellmapi:gemini-2.5-flash"],
    )
    chain = build_provider_chain()
    assert [p.name for p in chain] == ["ollama-cloud", "ollama-cloud", "freellmapi"]
    assert [p._model for p in chain] == [
        "glm-5.2",
        "gemma4:31b",
        "gemini-2.5-flash",
    ]


def test_build_provider_chain_old_name_only_format_uses_default_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compatibility: a bare name with no ':' resolves to the
    provider's configured default model."""
    monkeypatch.setattr(summarizer.settings, "freellmapi_model", "gpt-4o-mini")
    monkeypatch.setattr(summarizer.settings, "ollama_model", "glm-5.2")
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["ollama-cloud", "freellmapi"])
    chain = build_provider_chain()
    assert [p.name for p in chain] == ["ollama-cloud", "freellmapi"]
    assert [p._model for p in chain] == ["glm-5.2", "gpt-4o-mini"]


def test_build_provider_chain_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["codex", "bogus:x"])
    with pytest.raises(ValueError) as exc:
        build_provider_chain()
    assert "bogus" in str(exc.value)


def test_default_summary_providers_is_lightweight_http_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#388: the default chain is the lightweight direct-HTTP chain
    glm-5.2 → gemma4:31b → gemini-2.5-flash. The heavy CLI harnesses (codex,
    claude) are demoted to opt-in via SCRIBE_SUMMARY_PROVIDERS, so neither is
    in the default. Regression-keeps the #250 invariant that `claude` (CLI not
    installed in the runtime image) never sits in the default chain."""
    from scribe.config import Settings

    monkeypatch.delenv("SCRIBE_SUMMARY_PROVIDERS", raising=False)
    defaults = Settings().summary_providers
    assert defaults == [
        "ollama-cloud:glm-5.2",
        "ollama-cloud:gemma4:31b",
        "freellmapi:gemini-2.5-flash",
    ]
    assert "claude" not in defaults
    assert "codex" not in defaults


def test_default_chain_builds_in_order_with_per_provider_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default `provider:model` chain instantiates in order. Two
    ollama-cloud instances carry different models but share the backend name."""
    from scribe.config import Settings

    monkeypatch.delenv("SCRIBE_SUMMARY_PROVIDERS", raising=False)
    chain = build_provider_chain(Settings())
    assert [p.name for p in chain] == [
        "ollama-cloud",
        "ollama-cloud",
        "freellmapi",
    ]
    assert [getattr(p, "_model", None) for p in chain] == [
        "glm-5.2",
        "gemma4:31b",
        "gemini-2.5-flash",
    ]


def test_chain_falls_through_failing_model_to_next_ollama_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance (fallback): two ollama-cloud models share the backend name but
    are independent chain positions — a 5xx on glm-5.2 advances to gemma4:31b,
    which succeeds. Within one run the shared breaker does not trip (one failure
    < threshold), so the second model is still attempted."""
    monkeypatch.setattr(summarizer.settings, "ollama_base_url", "http://ollama:11434/v1")
    monkeypatch.setattr(summarizer.settings, "ollama_api_key", "")
    monkeypatch.setattr(summarizer.settings, "ollama_model", "glm-5.2")
    monkeypatch.setattr(summarizer.settings, "ollama_timeout_secs", 30)
    monkeypatch.setattr(
        summarizer.settings,
        "summary_providers",
        ["ollama-cloud:glm-5.2", "ollama-cloud:gemma4:31b"],
    )

    seen_models: list[str] = []

    def fake_post(url, *, content, headers, timeout):  # noqa: ARG001
        model = json.loads(content)["model"]
        seen_models.append(model)
        if model == "glm-5.2":
            return httpx.Response(503, text="overloaded", request=httpx.Request("POST", url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _OK_MARKDOWN}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(summary_providers.httpx, "post", fake_post)

    chain = build_provider_chain()
    result = summarize_with_chain(chain, "prompt")
    assert isinstance(result, SummaryResult)
    assert seen_models == ["glm-5.2", "gemma4:31b"]
    # The shared ollama-cloud breaker only saw one failure this run → still closed.
    assert summary_providers.get_breaker("ollama-cloud").state == "closed"


# ---------- summarize() integration with mocked providers --------------------


class _ScriptedProvider:
    """Records calls and replays a scripted list of results/exceptions."""

    def __init__(self, name: str, script: list[Any]) -> None:
        self.name = name
        self.script = list(script)
        self.calls = 0
        # Codex-only field surfaced to the wrapper for token-revoked alerting.
        self.last_token_revoked_stderr: str | None = None

    def summarize(self, prompt: str) -> SummaryResult:  # noqa: ARG002
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_summary() -> SummaryResult:
    return SummaryResult(
        summary_md=_OK_MARKDOWN,
        tags=["ai", "infra"],
        short_description="x",
    )


@pytest.fixture()
def _stub_prompt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "transcript-summary.v1.md").write_text(
        "Prompt {date} {transcript_slug}", encoding="utf-8"
    )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")
    monkeypatch.setattr("scribe.pipeline.prompts.settings.prompt_dir", str(tmp_path))
    return tmp_path


def _patch_chain(
    monkeypatch: pytest.MonkeyPatch, providers: list[_ScriptedProvider]
) -> None:
    monkeypatch.setattr(summarizer, "build_provider_chain", lambda: providers)


def test_summarize_succeeds_on_first_provider_skips_rest(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _ScriptedProvider("codex", [_ok_summary()])
    claude = _ScriptedProvider("claude", [_ok_summary()])
    free = _ScriptedProvider("freellmapi", [_ok_summary()])
    _patch_chain(monkeypatch, [codex, claude, free])

    result = summarizer.summarize("transcript", title="Title")

    assert isinstance(result, SummaryResult)
    assert codex.calls == 1
    assert claude.calls == 0
    assert free.calls == 0


def test_summarize_falls_through_codex_usage_limit_to_claude(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _ScriptedProvider(
        "codex",
        [ProviderUsageLimitError(reason="codex_usage_limit", details="limit")],
    )
    claude = _ScriptedProvider("claude", [_ok_summary()])
    free = _ScriptedProvider("freellmapi", [_ok_summary()])
    _patch_chain(monkeypatch, [codex, claude, free])

    result = summarizer.summarize("transcript", title="Title")

    assert isinstance(result, SummaryResult)
    assert codex.calls == 1
    assert claude.calls == 1
    assert free.calls == 0


def test_summarize_falls_through_codex_and_claude_to_freellmapi(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _ScriptedProvider(
        "codex",
        [ProviderUsageLimitError(reason="codex_usage_limit", details="codex limit")],
    )
    claude = _ScriptedProvider(
        "claude",
        [ProviderUsageLimitError(reason="claude_usage_limit", details="claude limit")],
    )
    free = _ScriptedProvider("freellmapi", [_ok_summary()])
    _patch_chain(monkeypatch, [codex, claude, free])

    result = summarizer.summarize("transcript", title="Title")

    assert isinstance(result, SummaryResult)
    assert codex.calls == 1
    assert claude.calls == 1
    assert free.calls == 1


def test_summarize_all_fail_raises_summarize_error_with_attempts(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _ScriptedProvider(
        "codex",
        [ProviderUsageLimitError(reason="codex_usage_limit", details="codex limit")],
    )
    claude = _ScriptedProvider(
        "claude",
        [ProviderUnavailableError(reason="claude_unavailable", details="claude down")],
    )
    free = _ScriptedProvider(
        "freellmapi",
        [ProviderTimeoutError(reason="timeout", details="free timed out")],
    )
    _patch_chain(monkeypatch, [codex, claude, free])

    alerts: list[str] = []
    monkeypatch.setattr(summarizer, "send_admin_alert", lambda text: alerts.append(text))

    with pytest.raises(summarizer.SummarizeError) as exc:
        summarizer.summarize("transcript", title="Title")

    msg = str(exc.value)
    assert "codex" in msg
    assert "claude" in msg
    assert "freellmapi" in msg
    # Token-revoked alert must NOT fire when codex's failure was usage-limit,
    # not token-revoked.
    assert alerts == []


def test_summarize_chain_failure_with_codex_token_revoked_alerts_once(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _ScriptedProvider(
        "codex",
        [ProviderUnavailableError(reason="codex_token_revoked", details="oauth dead")],
    )
    codex.last_token_revoked_stderr = "Encountered invalidated oauth token ..."
    claude = _ScriptedProvider(
        "claude",
        [ProviderUnavailableError(reason="claude_unavailable", details="down")],
    )
    free = _ScriptedProvider(
        "freellmapi",
        [ProviderError(reason="freellmapi_http_error", details="500")],
    )
    _patch_chain(monkeypatch, [codex, claude, free])

    alerts: list[str] = []
    monkeypatch.setattr(summarizer, "send_admin_alert", lambda text: alerts.append(text))

    with pytest.raises(summarizer.CodexTokenRevokedError):
        summarizer.summarize("transcript", title="Title")

    assert len(alerts) == 1
    assert "codex" in alerts[0].lower()


def test_summarize_codex_token_revoked_but_claude_recovers_no_alert(
    _stub_prompt_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex fails with token-revoked, but claude recovers — no Telegram noise."""
    codex = _ScriptedProvider(
        "codex",
        [ProviderUnavailableError(reason="codex_token_revoked", details="oauth dead")],
    )
    codex.last_token_revoked_stderr = "token_revoked"
    claude = _ScriptedProvider("claude", [_ok_summary()])
    _patch_chain(monkeypatch, [codex, claude])

    alerts: list[str] = []
    monkeypatch.setattr(summarizer, "send_admin_alert", lambda text: alerts.append(text))

    result = summarizer.summarize("transcript", title="Title")
    assert isinstance(result, SummaryResult)
    assert alerts == []
