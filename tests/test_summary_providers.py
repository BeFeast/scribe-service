"""Tests for the per-provider summary backends + the fallback-chain
integration on top of `scribe.pipeline.summarizer.summarize`.

Covers:
  * CodexProvider: subprocess-level usage-limit / token-revoked / happy-path.
  * ClaudeProvider: same stderr-signature mapping pattern, on `claude` CLI.
  * FreeLLMAPIProvider: httpx 200 / 429 / 5xx / timeout mapping.
  * `summarize()` integration: the right number of providers get invoked for
    each combination of (success, usage-limit, …), and the Telegram alert
    only fires once after the WHOLE chain has failed.
"""
from __future__ import annotations

import subprocess
from typing import Any

import httpx
import pytest

from scribe import alerts
from scribe.pipeline import prompts, summarizer
from scribe.pipeline import summary_providers as sp

CANONICAL_SUMMARY_MD = (
    "---\n"
    "tags: [ai, systems]\n"
    'short_description: "Canonical summary used as fixture."\n'
    "---\n"
    "\n"
    "## TL;DR\n"
    "\n"
    "Body content.\n"
)


# =============================================================================
# Helpers
# =============================================================================


def _settings_stub(**overrides: Any) -> Any:
    """Minimal duck-typed settings object the providers consume."""

    class _S:
        codex_bin = "codex"
        codex_model = ""
        codex_reasoning = "low"
        codex_lock_path = "/tmp/scribe-test-codex.lock"
        codex_timeout_secs = 600
        claude_bin = "claude"
        claude_model = "opus[1m]"
        claude_effort = "xhigh"
        claude_timeout_secs = 600
        freellmapi_base_url = "http://10.10.0.13:13032/v1"
        freellmapi_api_key = "secret-key"
        freellmapi_model = "gpt-4o-mini"
        freellmapi_timeout_secs = 60
        summary_providers = ["codex", "claude", "freellmapi"]
        short_description_language = "ru"

    for k, v in overrides.items():
        setattr(_S, k, v)
    return _S()


# =============================================================================
# CodexProvider
# =============================================================================


def _make_codex_proc(stderr: str = "", stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=["codex"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_codex_provider_happy_path_returns_summary_result(tmp_path, monkeypatch):
    s = _settings_stub(codex_lock_path=str(tmp_path / "codex.lock"))
    provider = sp.CodexProvider(s)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        from pathlib import Path as _P
        idx = cmd.index("-o") + 1
        _P(cmd[idx]).write_text(CANONICAL_SUMMARY_MD, encoding="utf-8")
        return _make_codex_proc(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    result = provider.summarize("the prompt")
    assert result.tags == ["ai", "systems"]
    assert "Canonical summary used as fixture." in (result.short_description or "")


def test_codex_provider_usage_limit_stderr_raises_usage_limit(tmp_path, monkeypatch):
    s = _settings_stub(codex_lock_path=str(tmp_path / "codex.lock"))
    provider = sp.CodexProvider(s)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return _make_codex_proc(stderr="ERROR: usage limit hit, try again later", returncode=1)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderUsageLimitError) as exc_info:
        provider.summarize("x")
    assert "usage limit" in exc_info.value.details.lower()


def test_codex_provider_rate_limit_stderr_raises_usage_limit(tmp_path, monkeypatch):
    s = _settings_stub(codex_lock_path=str(tmp_path / "codex.lock"))
    provider = sp.CodexProvider(s)

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return _make_codex_proc(stderr="rate limit exceeded for org", returncode=1)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderUsageLimitError):
        provider.summarize("x")


def test_codex_provider_token_revoked_raises_unavailable_with_stderr_tail(tmp_path, monkeypatch):
    s = _settings_stub(codex_lock_path=str(tmp_path / "codex.lock"))
    provider = sp.CodexProvider(s)
    stderr = 'invalid_request_error", "code": "refresh_token_reused"'

    def fake_run(cmd, input, text, capture_output, timeout):  # noqa: A002, ARG001
        return _make_codex_proc(stderr=stderr, returncode=1)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderUnavailableError) as exc_info:
        provider.summarize("x")
    # The stderr_tail attribute is what the chain reuses to fire a Telegram
    # alert AFTER the whole chain has failed.
    assert "refresh_token_reused" in exc_info.value.stderr_tail


def test_codex_provider_timeout_raises_provider_timeout(tmp_path, monkeypatch):
    s = _settings_stub(codex_lock_path=str(tmp_path / "codex.lock"))
    provider = sp.CodexProvider(s)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=600)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderTimeoutError):
        provider.summarize("x")


# =============================================================================
# ClaudeProvider
# =============================================================================


def _make_claude_proc(stderr: str = "", stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_claude_provider_happy_path(monkeypatch):
    provider = sp.ClaudeProvider(_settings_stub())

    def fake_run(cmd, text, capture_output, timeout):  # noqa: ARG001
        return _make_claude_proc(stdout=CANONICAL_SUMMARY_MD, returncode=0)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    result = provider.summarize("prompt+transcript")
    assert result.tags == ["ai", "systems"]


def test_claude_provider_invokes_cli_with_model_and_effort(monkeypatch):
    s = _settings_stub(claude_bin="claude", claude_model="opus[1m]", claude_effort="xhigh")
    provider = sp.ClaudeProvider(s)
    seen: dict[str, Any] = {}

    def fake_run(cmd, text, capture_output, timeout):  # noqa: ARG001
        seen["cmd"] = cmd
        return _make_claude_proc(stdout=CANONICAL_SUMMARY_MD, returncode=0)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    provider.summarize("hello")

    cmd = seen["cmd"]
    assert cmd[0] == "claude"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus[1m]"
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "xhigh"
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "hello"


def test_claude_provider_usage_limit_raises_usage_limit(monkeypatch):
    provider = sp.ClaudeProvider(_settings_stub())

    def fake_run(cmd, text, capture_output, timeout):  # noqa: ARG001
        return _make_claude_proc(stderr="Anthropic rate limit reached", returncode=2)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderUsageLimitError):
        provider.summarize("x")


def test_claude_provider_missing_binary_raises_unavailable(monkeypatch):
    provider = sp.ClaudeProvider(_settings_stub(claude_bin="/no/such/claude"))

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderUnavailableError):
        provider.summarize("x")


def test_claude_provider_timeout(monkeypatch):
    provider = sp.ClaudeProvider(_settings_stub())

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=600)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    with pytest.raises(sp.ProviderTimeoutError):
        provider.summarize("x")


# =============================================================================
# FreeLLMAPIProvider
# =============================================================================


class _FakeHttpxResponse:
    def __init__(self, status_code: int, json_data: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text or (str(json_data) if json_data is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeHttpxClient:
    def __init__(self, *, response: _FakeHttpxResponse | None = None,
                 raise_exc: BaseException | None = None, **_: Any) -> None:
        self._response = response
        self._raise = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *_: Any):
        return None

    def post(self, url, json, headers):  # noqa: A002
        if self._raise is not None:
            raise self._raise
        return self._response


def _install_fake_httpx(monkeypatch, **kwargs: Any) -> None:
    monkeypatch.setattr(sp.httpx, "Client", lambda **_kw: _FakeHttpxClient(**kwargs))


def test_freellmapi_provider_happy_path(monkeypatch):
    response = _FakeHttpxResponse(
        200,
        json_data={"choices": [{"message": {"content": CANONICAL_SUMMARY_MD}}]},
    )
    _install_fake_httpx(monkeypatch, response=response)
    provider = sp.FreeLLMAPIProvider(_settings_stub())
    result = provider.summarize("prompt")
    assert result.tags == ["ai", "systems"]


def test_freellmapi_provider_429_is_usage_limit(monkeypatch):
    _install_fake_httpx(
        monkeypatch,
        response=_FakeHttpxResponse(429, text="too many requests"),
    )
    provider = sp.FreeLLMAPIProvider(_settings_stub())
    with pytest.raises(sp.ProviderUsageLimitError):
        provider.summarize("p")


def test_freellmapi_provider_500_is_provider_error(monkeypatch):
    _install_fake_httpx(
        monkeypatch,
        response=_FakeHttpxResponse(503, text="upstream timeout"),
    )
    provider = sp.FreeLLMAPIProvider(_settings_stub())
    with pytest.raises(sp.ProviderError) as exc_info:
        provider.summarize("p")
    # 5xx should NOT subclass to usage-limit/unavailable/timeout.
    assert not isinstance(
        exc_info.value,
        (sp.ProviderUsageLimitError, sp.ProviderUnavailableError, sp.ProviderTimeoutError),
    )
    assert exc_info.value.reason == "freellmapi_5xx"


def test_freellmapi_provider_timeout(monkeypatch):
    _install_fake_httpx(
        monkeypatch,
        raise_exc=httpx.TimeoutException("read timeout"),
    )
    provider = sp.FreeLLMAPIProvider(_settings_stub())
    with pytest.raises(sp.ProviderTimeoutError):
        provider.summarize("p")


def test_freellmapi_provider_missing_api_key_raises_unavailable():
    provider = sp.FreeLLMAPIProvider(_settings_stub(freellmapi_api_key=""))
    with pytest.raises(sp.ProviderUnavailableError):
        provider.summarize("p")


def test_freellmapi_provider_transport_error_is_unavailable(monkeypatch):
    _install_fake_httpx(
        monkeypatch,
        raise_exc=httpx.ConnectError("connection refused"),
    )
    provider = sp.FreeLLMAPIProvider(_settings_stub())
    with pytest.raises(sp.ProviderUnavailableError):
        provider.summarize("p")


# =============================================================================
# summarize() integration — fallback chain
# =============================================================================


@pytest.fixture()
def patched_summarize(monkeypatch, tmp_path):
    """Wire up prompt loading + lock path so summarize() can build a chain."""
    (tmp_path / "transcript-summary.v1.md").write_text(
        "Prompt {date} {transcript_slug}", encoding="utf-8"
    )
    (tmp_path / "transcript-summary.active").write_text("v1\n", encoding="utf-8")
    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    monkeypatch.setattr(summarizer.settings, "codex_lock_path", str(tmp_path / "codex.lock"))
    monkeypatch.setattr(summarizer.settings, "codex_bin", "codex")
    monkeypatch.setattr(summarizer.settings, "codex_model", "")
    monkeypatch.setattr(summarizer.settings, "codex_reasoning", "low")
    monkeypatch.setattr(summarizer.settings, "claude_bin", "claude")
    monkeypatch.setattr(summarizer.settings, "freellmapi_api_key", "sk-test")
    return tmp_path


class _CallTracker:
    """Counts calls per provider name. Each entry mirrors `name -> count`."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, name: str):
        self.calls.append(name)


def _patch_codex(monkeypatch, tracker, *, payload: str | BaseException):
    """Replace `CodexProvider.summarize` to count calls and emit `payload`."""

    def fake_summarize(self, prompt):  # noqa: ARG001
        tracker("codex")
        if isinstance(payload, BaseException):
            raise payload
        from scribe.pipeline.summary_validator import validate_and_canonicalize
        return validate_and_canonicalize(payload)

    monkeypatch.setattr(sp.CodexProvider, "summarize", fake_summarize)


def _patch_claude(monkeypatch, tracker, *, payload: str | BaseException):
    def fake_summarize(self, prompt):  # noqa: ARG001
        tracker("claude")
        if isinstance(payload, BaseException):
            raise payload
        from scribe.pipeline.summary_validator import validate_and_canonicalize
        return validate_and_canonicalize(payload)

    monkeypatch.setattr(sp.ClaudeProvider, "summarize", fake_summarize)


def _patch_freellmapi(monkeypatch, tracker, *, payload: str | BaseException):
    def fake_summarize(self, prompt):  # noqa: ARG001
        tracker("freellmapi")
        if isinstance(payload, BaseException):
            raise payload
        from scribe.pipeline.summary_validator import validate_and_canonicalize
        return validate_and_canonicalize(payload)

    monkeypatch.setattr(sp.FreeLLMAPIProvider, "summarize", fake_summarize)


def test_summarize_codex_succeeds_first_no_fallback_called(patched_summarize, monkeypatch):
    """Happy path: codex returns a valid summary; claude/freellmapi never get
    constructed-and-called. This is the steady-state behaviour."""
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    _patch_claude(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    _patch_freellmapi(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert tracker.calls == ["codex"]


def test_summarize_falls_through_codex_usage_limit_to_claude(patched_summarize, monkeypatch):
    """Codex hits usage limit (the 2026-05-27 outage scenario): claude is
    invoked next and wins. FreeLLMAPI is never touched."""
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("codex usage limit"))
    _patch_claude(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    _patch_freellmapi(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert tracker.calls == ["codex", "claude"]


def test_summarize_falls_through_codex_and_claude_to_freellmapi(patched_summarize, monkeypatch):
    """Codex + claude both report usage limits — freellmapi takes over."""
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("codex usage limit"))
    _patch_claude(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("claude usage limit"))
    _patch_freellmapi(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert tracker.calls == ["codex", "claude", "freellmapi"]


def test_summarize_all_three_fail_raises_summarize_error(patched_summarize, monkeypatch):
    """When every provider raises, callers see a single `SummarizeError`
    whose message lists all three reasons. The worker uses this single
    exception to fail the job and log the diagnostic surface."""
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("codex usage limit"))
    _patch_claude(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("claude usage limit"))
    _patch_freellmapi(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("freellmapi usage limit"))

    with pytest.raises(summarizer.SummarizeError) as exc_info:
        summarizer.summarize("transcript body", title="Test")
    msg = str(exc_info.value)
    assert "codex" in msg and "claude" in msg and "freellmapi" in msg
    assert tracker.calls == ["codex", "claude", "freellmapi"]


def test_summarize_token_revoked_fires_telegram_alert_only_after_chain_exhausted(
    patched_summarize, monkeypatch
):
    """A revoked codex token alone must NOT fire an alert (otherwise the
    operator gets paged on every job during a 3-day outage). The alert fires
    exactly once AFTER the whole chain has failed."""
    tracker = _CallTracker()
    alerts_sent: list[str] = []
    monkeypatch.setattr(summarizer, "_alert_token_revoked", lambda tail: alerts_sent.append(tail))

    _patch_codex(
        monkeypatch, tracker,
        payload=sp.ProviderUnavailableError(
            "token revoked", stderr_tail="refresh_token_reused — re-login required"
        ),
    )
    # Both fallbacks also fail so we exhaust the chain.
    _patch_claude(monkeypatch, tracker, payload=sp.ProviderUsageLimitError("claude usage limit"))
    _patch_freellmapi(monkeypatch, tracker, payload=sp.ProviderError(reason="boom", details="freellmapi exploded"))

    with pytest.raises(summarizer.SummarizeError):
        summarizer.summarize("transcript body", title="Test")

    assert len(alerts_sent) == 1
    assert "refresh_token_reused" in alerts_sent[0]


def test_summarize_codex_revoked_then_claude_succeeds_suppresses_alert(
    patched_summarize, monkeypatch
):
    """Codex token revoked but claude rescues the job → NO Telegram alert.
    The operator only needs to re-login eventually; we don't bother them on
    successful jobs."""
    tracker = _CallTracker()
    alerts_sent: list[str] = []
    monkeypatch.setattr(summarizer, "_alert_token_revoked", lambda tail: alerts_sent.append(tail))

    _patch_codex(
        monkeypatch, tracker,
        payload=sp.ProviderUnavailableError(
            "token revoked", stderr_tail="refresh_token_reused"
        ),
    )
    _patch_claude(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    _patch_freellmapi(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert alerts_sent == []


def test_summarize_respects_provider_order_from_settings(patched_summarize, monkeypatch):
    """Configuring `SCRIBE_SUMMARY_PROVIDERS=freellmapi,codex` reverses the
    order — freellmapi is tried first."""
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["freellmapi", "codex"])
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    _patch_freellmapi(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert tracker.calls == ["freellmapi"]


def test_summarize_skips_unknown_provider_name(patched_summarize, monkeypatch):
    """Operator typo / leftover deprecated name: log + skip, don't crash."""
    monkeypatch.setattr(summarizer.settings, "summary_providers", ["bogus", "claude"])
    tracker = _CallTracker()
    _patch_claude(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)
    result = summarizer.summarize("transcript body", title="Test")
    assert result.tags == ["ai", "systems"]
    assert tracker.calls == ["claude"]


def test_summarize_empty_provider_list_raises_summarize_error(patched_summarize, monkeypatch):
    monkeypatch.setattr(summarizer.settings, "summary_providers", [])
    with pytest.raises(summarizer.SummarizeError):
        summarizer.summarize("transcript body", title="Test")


def test_summarize_lock_timeout_propagates_without_chain_fallback(patched_summarize, monkeypatch):
    """`LockTimeoutError` must surface to the API handler unchanged so it can
    map to a 503-style response. We must NOT fall through to claude — the
    contention will resolve on its own."""
    tracker = _CallTracker()
    _patch_codex(monkeypatch, tracker, payload=sp.LockTimeoutError("locked"))
    _patch_claude(monkeypatch, tracker, payload=CANONICAL_SUMMARY_MD)

    with pytest.raises(summarizer.LockTimeoutError):
        summarizer.summarize("transcript body", title="Test")
    assert tracker.calls == ["codex"]


# =============================================================================
# Settings — comma-separated SCRIBE_SUMMARY_PROVIDERS env parsing
# =============================================================================


def test_settings_parse_summary_providers_from_comma_string():
    from scribe.config import Settings
    s = Settings(SUMMARY_PROVIDERS="codex,Claude,FREELLMAPI")
    assert s.summary_providers == ["codex", "claude", "freellmapi"]


def test_settings_parse_summary_providers_default_list():
    from scribe.config import Settings
    s = Settings()
    assert s.summary_providers == ["codex", "claude", "freellmapi"]


def test_settings_parse_summary_providers_accepts_python_list():
    from scribe.config import Settings
    s = Settings(summary_providers=["codex"])
    assert s.summary_providers == ["codex"]


# Make sure no test in this module accidentally lets a real alert escape.
@pytest.fixture(autouse=True)
def _suppress_admin_alert(monkeypatch):
    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "")
