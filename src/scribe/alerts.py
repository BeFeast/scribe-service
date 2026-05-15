"""Operational alerts — optional Telegram channel for operator-visible events.

If SCRIBE_ADMIN_TELEGRAM_BOT_TOKEN + SCRIBE_ADMIN_TELEGRAM_CHAT_ID are both
set in env, `send_admin_alert(text)` POSTs a short message to that chat.
Otherwise it's a no-op (logs at WARNING). Designed to be fire-and-forget
from anywhere in the codebase — never raises.

This is intentionally a separate Telegram channel from the consumer-facing
shtrudel bot: scribe is consumer-agnostic, and ops noise shouldn't leak to
end users.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request

from scribe.config import settings

log = logging.getLogger("scribe.alerts")


def send_admin_alert(text: str) -> bool:
    """Send `text` to the admin Telegram channel. Returns True on success.
    Never raises — alerts are best-effort. Logs the failure at WARNING."""
    token = settings.admin_telegram_bot_token.strip()
    chat_id = settings.admin_telegram_chat_id.strip()
    if not token or not chat_id:
        log.warning("admin alert suppressed (no telegram creds): %s", text[:200])
        return False
    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        log.warning("admin alert delivery failed: %s — %s", exc, text[:200])
        return False
