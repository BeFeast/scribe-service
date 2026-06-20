"""Tests for the secret-redaction filter on the logging path (#348)."""
from __future__ import annotations

import json
import logging
import sys

from scribe.obs.live_logs import configure_redaction, payload_from_record
from scribe.obs.logging import JsonFormatter

_SECRET = "SUPER-SECRET-TOKEN-abc123"
_MESSAGE_SECRET = "MESSAGE-SECRET-xyz789"


def _record(msg: str, *, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise ValueError(f"boom {_SECRET}")
        except ValueError:
            exc_info = sys.exc_info()
    return logging.LogRecord(
        name="scribe.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )


def test_secret_in_exception_traceback_is_redacted():
    configure_redaction([_SECRET])
    record = _record("job failed", exc=True)
    payload = payload_from_record(record)

    assert _SECRET not in payload["exc"]
    assert "[redacted]" in payload["exc"]


def test_secret_in_message_is_redacted():
    configure_redaction([_MESSAGE_SECRET])
    payload = payload_from_record(_record(f"got {_MESSAGE_SECRET} value"))

    assert _MESSAGE_SECRET not in payload["msg"]
    assert "[redacted]" in payload["msg"]


def test_secret_in_extra_string_is_redacted():
    configure_redaction([_SECRET])
    record = _record("ok")
    record.secret_field = f"token={_SECRET}"
    payload = payload_from_record(record)

    assert _SECRET not in payload["secret_field"]
    assert "[redacted]" in payload["secret_field"]


def test_json_formatter_output_contains_no_secret():
    configure_redaction([_SECRET])
    line = JsonFormatter().format(_record("job failed", exc=True))
    parsed = json.loads(line)

    rendered = json.dumps(parsed, ensure_ascii=False)
    assert _SECRET not in rendered
    assert "[redacted]" in rendered


def test_no_secrets_registered_passes_through_unchanged():
    configure_redaction([])
    payload = payload_from_record(_record("plain message"))

    assert payload["msg"] == "plain message"


def test_short_values_are_not_registered_as_secrets():
    # A trivially short value must not be treated as a secret, otherwise
    # common substrings would be redacted spuriously.
    configure_redaction(["abc"])
    payload = payload_from_record(_record("the abc token"))

    assert payload["msg"] == "the abc token"

