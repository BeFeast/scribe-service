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


def test_reap_reason_cost_fires_before_max_age(monkeypatch):
    """A runaway-cost scribe instance is reaped regardless of age (#355)."""
    counter = _patch_counter(monkeypatch)
    calls: list[tuple[str, str]] = []
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 700,
                        # 5 min old — well under max_age (60 min).
                        "label": "498a89b78308-scribe-whisper-20260527T115500Z",
                        "actual_status": "running",
                        "dph_base": 2.0,  # 40x baseline (0.05) -> cost runaway.
                        "storage_total_cost": 0.0,
                    }
                ]
            }
        return {}

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)
    monkeypatch.setattr(vast_reaper.settings, "vast_budget_baseline_usd_per_hour", 0.05)
    monkeypatch.setattr(vast_reaper.settings, "vast_orphan_reaper_cost_multiplier", 10.0)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1
    assert ("DELETE", "/instances/700/") in calls
    assert counter.value == 1


def test_reap_reason_stuck_fires_before_max_age(monkeypatch):
    """A scribe instance stuck initializing is reaped before max_age (#355)."""
    counter = _patch_counter(monkeypatch)
    calls: list[tuple[str, str]] = []
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)

    def fake_vast(_api_key, method, path, payload=None, timeout=45):
        calls.append((method, path))
        if method == "GET":
            return {
                "instances": [
                    {
                        "id": 800,
                        # 20 min old — under max_age (60) but over stuck (15).
                        "label": "498a89b78308-scribe-whisper-20260527T114000Z",
                        "actual_status": "loading",
                        "dph_base": 0.04,  # under cost threshold.
                        "storage_total_cost": 0.0,
                    }
                ]
            }
        return {}

    monkeypatch.setattr(vast_reaper, "_vast", fake_vast)
    monkeypatch.setattr(vast_reaper.settings, "vast_budget_baseline_usd_per_hour", 0.05)
    monkeypatch.setattr(vast_reaper.settings, "vast_orphan_reaper_cost_multiplier", 10.0)
    monkeypatch.setattr(vast_reaper.settings, "vast_orphan_reaper_stuck_minutes", 15)

    assert vast_reaper.reap_vast_orphans(api_key="vast-key", max_age_minutes=60, now=now) == 1
    assert ("DELETE", "/instances/800/") in calls
    assert counter.value == 1


def test_reap_reason_predicate_cost_takes_priority():
    """Pure predicate: cost wins over stuck/age; non-scribe is skipped."""
    now = dt.datetime(2026, 5, 27, 12, 0, tzinfo=dt.UTC)
    max_age = dt.timedelta(minutes=60)
    stuck = dt.timedelta(minutes=15)
    baseline = 0.05
    cost_multiplier = 10.0

    cost_instance = {
        "id": 1,
        "label": "x-scribe-whisper-20260527T115900Z",
        "actual_status": "running",
        "dph_base": 2.0,
    }
    assert (
        vast_reaper.reap_reason_for(
            cost_instance,
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck,
        )
        == "cost"
    )

    stuck_instance = {
        "id": 2,
        "label": "x-scribe-whisper-20260527T114000Z",
        "actual_status": "loading",
        "dph_base": 0.01,
    }
    assert (
        vast_reaper.reap_reason_for(
            stuck_instance,
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck,
        )
        == "stuck"
    )

    # Stuck but younger than stuck_threshold is not reaped.
    young_stuck = {
        "id": 3,
        "label": "x-scribe-whisper-20260527T115900Z",
        "actual_status": "loading",
        "dph_base": 0.01,
    }
    assert (
        vast_reaper.reap_reason_for(
            young_stuck,
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck,
        )
        is None
    )

    # Old running instance -> age.
    old_instance = {
        "id": 4,
        "label": "x-scribe-whisper-20260527T090000Z",
        "actual_status": "running",
        "dph_base": 0.04,
    }
    assert (
        vast_reaper.reap_reason_for(
            old_instance,
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck,
        )
        == "age"
    )

    # Non-scribe instance is never reaped.
    assert (
        vast_reaper.reap_reason_for(
            {"id": 5, "label": "other", "actual_status": "running", "dph_base": 99.0},
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck,
        )
        is None
    )

    # Disabled cost multiplier (0) suppresses cost reaping: an old,
    # high-cost instance falls through to age.
    old_costly = {
        "id": 6,
        "label": "x-scribe-whisper-20260527T090000Z",
        "actual_status": "running",
        "dph_base": 2.0,
    }
    assert (
        vast_reaper.reap_reason_for(
            old_costly,
            now=now,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=0.0,
            stuck_threshold=stuck,
        )
        == "age"
    )
