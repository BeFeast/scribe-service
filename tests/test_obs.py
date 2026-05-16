"""Tests for scribe.obs — metrics exposition + structured JSON logging."""
from __future__ import annotations

import io
import json
import logging

from scribe.obs import logging as obs_logging
from scribe.obs import metrics
from scribe.obs.live_logs import JobLogBuffer, JobLogBufferHandler, job_log_buffer


def test_metrics_export_contains_scribe_metrics():
    body, ctype = metrics.export()
    assert "text/plain" in ctype
    text = body.decode()
    # Every collector this module defines must show up in the exposition.
    assert "scribe_job_status_transitions_total" in text
    assert "scribe_transcripts_total" in text
    assert "scribe_stage_duration_seconds" in text
    assert "scribe_vast_spend_usd_total" in text
    assert "scribe_worker_queue_depth" in text
    assert "scribe_last_success_timestamp_seconds" in text
    assert "scribe_codex_token_revoked_total" in text
    assert "scribe_daily_spend_usd" in text
    assert "scribe_daily_spend_cap_pct" in text
    assert "scribe_webhook_delivery_latency_seconds" in text
    assert "scribe_webhook_attempts_total" in text


def test_compute_daily_spend_cap_pct_math():
    """Sanity-check the percent math used by the alert metric."""
    # 80% threshold: exactly at the alert boundary.
    assert metrics.compute_daily_spend_cap_pct(4.0, 5.0) == 80.0
    # Under cap.
    assert metrics.compute_daily_spend_cap_pct(1.25, 5.0) == 25.0
    # Zero spend.
    assert metrics.compute_daily_spend_cap_pct(0.0, 5.0) == 0.0
    # Over cap clamps nothing — alert rule operates on >= 80.
    assert metrics.compute_daily_spend_cap_pct(7.5, 5.0) == 150.0


def test_compute_daily_spend_cap_pct_cap_disabled():
    """cap <= 0 means the cap is disabled — gauge must return 0, not divide by zero."""
    assert metrics.compute_daily_spend_cap_pct(3.0, 0.0) == 0.0
    assert metrics.compute_daily_spend_cap_pct(0.0, 0.0) == 0.0
    assert metrics.compute_daily_spend_cap_pct(3.0, -1.0) == 0.0


def test_daily_spend_gauges_reflect_set_values():
    """The gauges are sampled in routes.py; verify .set() round-trips through exposition."""
    metrics.daily_spend_usd.set(4.2)
    metrics.daily_spend_cap_pct.set(84.0)
    text = metrics.export()[0].decode()
    # gauges are emitted unlabelled, so the bare metric name + value is the match.
    assert "scribe_daily_spend_usd 4.2" in text
    assert "scribe_daily_spend_cap_pct 84.0" in text


def test_metrics_labelled_counter_increments():
    """job_status_transitions is a labelled counter — verify .inc() rolls
    through to the exposition for the labelled child."""
    before = _value_with_label("scribe_job_status_transitions_total", status="queued")
    metrics.job_status_transitions.labels(status="queued").inc()
    after = _value_with_label("scribe_job_status_transitions_total", status="queued")
    assert after == before + 1


def _value_with_label(name: str, **labels: str) -> float:
    """Pull the current value of a labelled metric from the exposition body."""
    body, _ = metrics.export()
    needle = f"{name}{{{','.join(f'{k}=\"{v}\"' for k, v in labels.items())}}}"
    for line in body.decode().splitlines():
        if line.startswith(needle):
            return float(line.split()[-1])
    return 0.0


def test_json_formatter_emits_one_json_per_record():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs_logging.JsonFormatter())

    log = logging.Logger("scribe.test", level=logging.INFO)
    log.addHandler(handler)
    log.info("hello", extra={"job_id": 42, "stage": "summary"})

    line = buf.getvalue().strip()
    payload = json.loads(line)  # raises if not valid JSON
    assert payload["msg"] == "hello"
    assert payload["lvl"] == "INFO"
    assert payload["logger"] == "scribe.test"
    assert payload["job_id"] == 42
    assert payload["stage"] == "summary"
    assert "ts" in payload


def test_json_formatter_includes_exc_when_present():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs_logging.JsonFormatter())

    log = logging.Logger("scribe.test", level=logging.ERROR)
    log.addHandler(handler)
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")

    payload = json.loads(buf.getvalue().strip())
    assert "exc" in payload
    assert "ValueError: boom" in payload["exc"]


def test_json_formatter_ignores_underscore_record_attrs():
    """Internal attrs like `_msg_template` shouldn't leak into output."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs_logging.JsonFormatter())

    log = logging.Logger("scribe.test", level=logging.INFO)
    log.addHandler(handler)
    log.info("plain", extra={"_internal": "skip-me", "public": "keep"})

    payload = json.loads(buf.getvalue().strip())
    assert "_internal" not in payload
    assert payload["public"] == "keep"


def test_job_log_buffer_is_fifo_bounded():
    buffer = JobLogBuffer(max_lines=2)
    buffer.append({"job_id": 7, "msg": "one"})
    buffer.append({"job_id": 7, "msg": "two"})
    buffer.append({"job_id": 7, "msg": "three"})

    version, lines = buffer.snapshot(7)
    assert version == 3
    assert [line["msg"] for line in lines] == ["two", "three"]


def test_job_log_buffer_handler_captures_worker_job_lines():
    job_log_buffer.clear()
    handler = JobLogBufferHandler()
    record = logging.LogRecord(
        "scribe.worker",
        logging.INFO,
        __file__,
        1,
        "hello %s",
        ("job",),
        None,
    )
    record.job_id = 42
    record.stage = "whisper"
    handler.emit(record)

    _, lines = job_log_buffer.snapshot(42)
    assert lines[0]["msg"] == "hello job"
    assert lines[0]["stage"] == "whisper"
    job_log_buffer.clear()
