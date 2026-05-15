"""Tests for scribe.alerts.send_admin_alert."""
from __future__ import annotations

from unittest.mock import patch

from scribe import alerts


def test_send_admin_alert_noop_when_unconfigured(monkeypatch):
    """No SCRIBE_ADMIN_TELEGRAM_* env -> returns False, never raises."""
    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "")
    assert alerts.send_admin_alert("anything") is False


def test_send_admin_alert_returns_false_with_partial_config(monkeypatch):
    """Token but no chat id (or vice versa) is still a no-op."""
    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "abc")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "")
    assert alerts.send_admin_alert("anything") is False

    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "123")
    assert alerts.send_admin_alert("anything") is False


def test_send_admin_alert_swallows_network_errors(monkeypatch):
    """Telegram outage must not crash whatever called send_admin_alert."""
    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "abc")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "123")

    def boom(*args, **kwargs):
        import urllib.error

        raise urllib.error.URLError("network down")

    with patch("urllib.request.urlopen", side_effect=boom):
        assert alerts.send_admin_alert("hello") is False


def test_send_admin_alert_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(alerts.settings, "admin_telegram_bot_token", "abc")
    monkeypatch.setattr(alerts.settings, "admin_telegram_chat_id", "123")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def read(self):
            return b"{}"

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        assert alerts.send_admin_alert("hello") is True
