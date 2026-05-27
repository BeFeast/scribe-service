from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from scribe.worker import vast_reaper


class _Counter:
    def __init__(self) -> None:
        self.value = 0

    def inc(self) -> None:
        self.value += 1


def _patch_counter(monkeypatch) -> _Counter:
    counter = _Counter()
    monkeypatch.setattr(vast_reaper.metrics, "vast_orphans_destroyed_total", counter)
    return counter


def test_reaper_filters_scribe_label_and_ignores_unrelated_instances(monkeypatch):
    counter = _patch_counter(monkeypatch)
    calls: list[tuple[str, str]] = []
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 100,
                        "label": "498a89b78308-scribe-whisper-20260527T090000Z",
                        "actual_status": "running",
                    },
                    {
                        "id": 200,
                        "label": "other-service-20260527T090000Z",
                        "actual_status": "running",
                    },
                ]
            }
        return {}

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1
    assert ("DELETE", "/instances/100/") in calls
    assert ("DELETE", "/instances/200/") not in calls
    assert counter.value == 1


def test_reaper_uses_age_threshold(monkeypatch):
    counter = _patch_counter(monkeypatch)
    calls: list[tuple[str, str]] = []
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 300,
                        "label": "498a89b78308-scribe-whisper-20260527T113000Z",
                        "actual_status": "running",
                    },
                    {
                        "id": 400,
                        "label": "498a89b78308-scribe-whisper-20260527T100000Z",
                        "actual_status": "running",
                    },
                ]
            }
        return {}

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1
    assert ("DELETE", "/instances/300/") not in calls
    assert ("DELETE", "/instances/400/") in calls
    assert counter.value == 1


def test_reaper_falls_back_to_start_date_when_label_has_no_timestamp(monkeypatch):
    counter = _patch_counter(monkeypatch)
    calls: list[tuple[str, str]] = []
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 500,
                        "label": "498a89b78308-scribe-whisper-manual",
                        "start_date": "2026-05-27T10:00:00Z",
                        "actual_status": "stopped",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1
    assert ("DELETE", "/instances/500/") in calls
    assert counter.value == 1


def test_reaper_counts_delete_attempt_and_logs_delete_error(monkeypatch, caplog):
    counter = _patch_counter(monkeypatch)
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 600,
                        "label": "498a89b78308-scribe-whisper-20260527T090000Z",
                        "actual_status": "running",
                    }
                ]
            }
        raise vast_reaper.VastReaperError(f"Vast API {method} {path}: HTTP 500: boom")

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)

    # scribe.obs.logging.configure() wipes root handlers on import, so caplog
    # can be broken in mixed-suite runs. Capture the logger calls directly.
    warnings: list[tuple[str, dict]] = []
    real_warning = vast_reaper.log.warning
    def capture(msg, *args, **kwargs):
        warnings.append((msg, kwargs.get("extra", {})))
        return real_warning(msg, *args, **kwargs)
    monkeypatch.setattr(vast_reaper.log, "warning", capture)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1

    assert counter.value == 1
    failure_msg, failure_extra = next((m, e) for m, e in warnings if "failed to destroy" in m)
    assert failure_extra["vast_instance_id"] == 600
    assert "HTTP 500" in failure_extra["error"]


def test_reaper_skips_when_api_key_missing(monkeypatch):
    counter = _patch_counter(monkeypatch)
    monkeypatch.setattr(vast_reaper.settings, "vast_api_key", "")
    monkeypatch.setattr(vast_reaper, "_vast", SimpleNamespace())

    assert vast_reaper.reap_vast_orphans() == 0
    assert counter.value == 0
