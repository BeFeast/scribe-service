from __future__ import annotations

import datetime as dt
import json
import urllib.request

import pytest
from sqlalchemy import delete

from scribe.db.models import Job, JobStatus, Transcript
from scribe.obs import metrics
from scribe.pipeline.whisper_client import WhisperError
from scribe.worker import vast_budget


def test_build_budget_check_uses_base_plus_storage_fixture_shape() -> None:
    check = vast_budget.build_budget_check(
        [
            {
                "id": 101,
                "label": "scribe-leak",
                "actual_status": "running",
                "dph_base": 0.14,
                "dph_total": 0.1421,
                "storage_total_cost": 0.0021,
            },
            {
                "id": 102,
                "label": "storage-only",
                "actual_status": "stopped",
                "storage_total_cost": 0.003,
            },
        ],
        baseline_usd_per_hour=0.05,
        alert_multiplier=5,
    )

    assert check.burn_rate_usd_per_hour == 0.1451
    assert check.threshold_usd_per_hour == 0.25
    assert check.is_anomaly is False
    assert check.instances[0].compute_usd_per_hour == 0.14
    assert check.instances[0].storage_usd_per_hour == 0.0021
    assert check.instances[1].total_usd_per_hour == 0.003


def test_build_budget_check_uses_dph_total_fallback_without_double_counting_storage() -> None:
    check = vast_budget.build_budget_check(
        [
            {
                "id": 201,
                "label": "docs-shape",
                "cur_state": "running",
                "dph_total": "0.2021",
                "storage_total_cost": "0.0021",
            },
            {
                "id": 202,
                "search": {"gpuCostPerHour": 0.11, "diskHour": 0.001},
            },
        ],
        baseline_usd_per_hour=0.05,
        alert_multiplier=5,
    )

    assert check.burn_rate_usd_per_hour == 0.3131
    assert check.threshold_usd_per_hour == 0.25
    assert check.is_anomaly is True
    assert check.instances[0].total_usd_per_hour == 0.2021
    assert check.instances[0].compute_usd_per_hour == 0.2
    assert check.instances[1].total_usd_per_hour == 0.111


def test_check_vast_budget_sets_gauge_logs_and_sends_alert(monkeypatch) -> None:
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget.settings, "vast_api_key", "fixture-key")
    monkeypatch.setattr(vast_budget.settings, "vast_budget_baseline_usd_per_hour", 0.05)
    monkeypatch.setattr(vast_budget.settings, "vast_budget_alert_multiplier", 2.0)
    monkeypatch.setattr(
        vast_budget,
        "fetch_instances",
        lambda _api_key: [
            {
                "id": 301,
                "label": "runaway",
                "actual_status": "running",
                "dph_base": 0.14,
                "storage_total_cost": 0.002,
            }
        ],
    )
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)
    # Isolate the anomaly path from the predictive-breach projection added
    # in #355 (which opens a DB session and may emit its own alert).
    monkeypatch.setattr(vast_budget, "_check_predictive_breach", lambda *_a, **_k: None)
    # scribe.obs.logging.configure() wipes root handlers on import, breaking
    # caplog in mixed suite runs. Capture log calls directly.
    warnings: list[tuple[str, dict]] = []
    real_warning = vast_budget.log.warning
    def capture(msg, *args, **kwargs):
        warnings.append((msg, kwargs.get("extra", {})))
        return real_warning(msg, *args, **kwargs)
    monkeypatch.setattr(vast_budget.log, "warning", capture)

    check = vast_budget.check_vast_budget()

    assert check is not None
    assert check.is_anomaly is True
    assert round(metrics.gauge_value(metrics.vast_burn_rate_usd_per_hour), 6) == 0.142
    assert len(alerts) == 1
    assert "Scribe Vast.ai burn-rate anomaly" in alerts[0]
    assert "$0.1420/hour" in alerts[0]
    assert any("vast burn rate anomaly" in m for m, _ in warnings)
    anomaly_extra = next(e for m, e in warnings if "vast burn rate anomaly" in m)
    assert anomaly_extra["vast_budget"]["instance_count"] == 1


def test_check_vast_budget_skips_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(vast_budget.settings, "vast_api_key", "")
    warnings: list[str] = []
    real_warning = vast_budget.log.warning
    def capture(msg, *args, **kwargs):
        warnings.append(msg)
        return real_warning(msg, *args, **kwargs)
    monkeypatch.setattr(vast_budget.log, "warning", capture)

    assert vast_budget.check_vast_budget() is None

    assert any("SCRIBE_VAST_API_KEY is not set" in m for m in warnings)


def _seed_transcript(session, *, video_id: str, vast_cost: float | None, created_at: dt.datetime) -> Transcript:
    job = Job(url=f"https://youtu.be/{video_id}", video_id=video_id, status=JobStatus.done)
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id,
        video_id=video_id,
        title="cap fixture",
        transcript_md="t",
        summary_md="s",
        vast_cost=vast_cost,
        created_at=created_at,
    )
    session.add(transcript)
    session.commit()
    return transcript


def test_monthly_vast_spend_sums_only_last_30_days(db_session) -> None:
    db_session.execute(delete(Transcript).where(Transcript.video_id.like("cap-%")))
    db_session.execute(delete(Job).where(Job.video_id.like("cap-%")))
    db_session.commit()

    now = dt.datetime.now(dt.UTC)
    _seed_transcript(db_session, video_id="cap-recent-1", vast_cost=0.10, created_at=now - dt.timedelta(days=1))
    _seed_transcript(db_session, video_id="cap-recent-2", vast_cost=0.04, created_at=now - dt.timedelta(days=15))
    _seed_transcript(db_session, video_id="cap-stale", vast_cost=99.0, created_at=now - dt.timedelta(days=60))
    _seed_transcript(db_session, video_id="cap-null", vast_cost=None, created_at=now - dt.timedelta(days=2))

    total = vast_budget.monthly_vast_spend_usd(db_session, now=now)

    assert round(total, 4) == 0.14


def test_in_flight_vast_reserve_scales_with_transcribing_count(db_session, monkeypatch) -> None:
    db_session.execute(delete(Transcript))
    db_session.execute(delete(Job))
    db_session.commit()
    monkeypatch.setattr(vast_budget.settings, "vast_max_job_cost", 0.25)
    monkeypatch.setattr(vast_budget.settings, "vast_max_price_per_hour", 3.0)

    baseline = vast_budget.in_flight_vast_reserve_usd(db_session)
    assert baseline == 0.0

    for idx in range(2):
        db_session.add(
            Job(
                url=f"https://youtu.be/cap-flight-{idx}",
                video_id=f"cap-flight-{idx}",
                status=JobStatus.transcribing,
            )
        )
    # A queued/done job must not be counted as in-flight.
    db_session.add(
        Job(
            url="https://youtu.be/cap-flight-queued",
            video_id="cap-flight-queued",
            status=JobStatus.queued,
        )
    )
    db_session.commit()

    reserve = vast_budget.in_flight_vast_reserve_usd(db_session)

    assert round(reserve, 4) == 0.50


def test_enforce_monthly_cap_raises_when_spend_plus_reserve_exceeds_cap(db_session, monkeypatch) -> None:
    db_session.execute(delete(Transcript).where(Transcript.video_id.like("cap-%")))
    db_session.execute(delete(Job).where(Job.video_id.like("cap-%")))
    db_session.commit()
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 0.30)
    monkeypatch.setattr(vast_budget.settings, "vast_max_job_cost", 0.25)
    monkeypatch.setattr(vast_budget.settings, "vast_max_price_per_hour", 3.0)
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)

    now = dt.datetime.now(dt.UTC)
    # Spent (0.15) alone is below the cap — only the in-flight reserve from
    # one transcribing job (0.25) pushes us over.
    _seed_transcript(db_session, video_id="cap-now", vast_cost=0.15, created_at=now - dt.timedelta(days=1))
    db_session.add(
        Job(
            url="https://youtu.be/cap-in-flight",
            video_id="cap-in-flight",
            status=JobStatus.transcribing,
        )
    )
    db_session.commit()

    with pytest.raises(WhisperError, match="Vast monthly cap reached"):
        vast_budget.enforce_monthly_cap(db_session, now=now)

    assert len(alerts) == 1
    assert "Vast monthly cap reached" in alerts[0]


def test_enforce_monthly_cap_noop_when_disabled(db_session, monkeypatch) -> None:
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 0.0)
    vast_budget.enforce_monthly_cap(db_session)


def test_enforce_monthly_cap_noop_under_cap(db_session, monkeypatch) -> None:
    db_session.execute(delete(Transcript).where(Transcript.video_id.like("cap-%")))
    db_session.execute(delete(Job).where(Job.video_id.like("cap-%")))
    db_session.commit()
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 100.0)
    monkeypatch.setattr(vast_budget.settings, "vast_max_job_cost", 0.25)
    monkeypatch.setattr(vast_budget.settings, "vast_max_price_per_hour", 3.0)

    now = dt.datetime.now(dt.UTC)
    _seed_transcript(db_session, video_id="cap-low", vast_cost=0.01, created_at=now - dt.timedelta(days=1))

    vast_budget.enforce_monthly_cap(db_session, now=now)


def test_fetch_instances_calls_v0_instances_endpoint(monkeypatch) -> None:
    requests: list[urllib.request.Request] = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"instances": [{"id": 401}]}).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        requests.append(request)
        assert timeout == 45
        return FakeResp()

    monkeypatch.setattr(vast_budget.urllib.request, "urlopen", fake_urlopen)

    assert vast_budget.fetch_instances("fixture-key") == [{"id": 401}]
    assert requests[0].full_url == "https://console.vast.ai/api/v0/instances/"
    assert requests[0].get_method() == "GET"
    assert requests[0].headers["Authorization"] == "Bearer fixture-key"


# --- Predictive burn-rate projection (#355) ---


def test_project_monthly_breach_flags_breach_within_horizon() -> None:
    now = dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC)
    # 12 USD left, burning 0.5/hr -> 24h to breach; horizon 30d -> breach.
    projection = vast_budget.project_monthly_breach(
        spent_usd=3.0,
        cap_usd=15.0,
        burn_rate_usd_per_hour=0.5,
        horizon_hours=30 * 24,
        now=now,
    )
    assert projection.will_breach is True
    assert round(projection.remaining_usd, 4) == 12.0
    assert round(projection.hours_to_breach, 4) == 24.0
    assert projection.projected_breach_at == now + dt.timedelta(hours=24.0)


def test_project_monthly_breach_no_breach_outside_horizon() -> None:
    now = dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC)
    # 12 USD left, burning 0.01/hr -> 1200h to breach; horizon 24h -> no breach.
    projection = vast_budget.project_monthly_breach(
        spent_usd=3.0,
        cap_usd=15.0,
        burn_rate_usd_per_hour=0.01,
        horizon_hours=24.0,
        now=now,
    )
    assert projection.will_breach is False
    assert round(projection.hours_to_breach, 2) == 1200.0
    assert projection.projected_breach_at is not None


def test_project_monthly_breach_disabled_when_cap_zero() -> None:
    now = dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC)
    projection = vast_budget.project_monthly_breach(
        spent_usd=3.0,
        cap_usd=0.0,
        burn_rate_usd_per_hour=0.5,
        horizon_hours=24.0,
        now=now,
    )
    assert projection.will_breach is False
    assert projection.hours_to_breach is None
    assert projection.projected_breach_at is None


def test_project_monthly_breach_no_breach_when_cap_already_reached() -> None:
    now = dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC)
    projection = vast_budget.project_monthly_breach(
        spent_usd=15.0,
        cap_usd=15.0,
        burn_rate_usd_per_hour=0.5,
        horizon_hours=24.0,
        now=now,
    )
    # Hard cap reached -> no predictive alert (enforce_monthly_cap handles it).
    assert projection.will_breach is False
    assert projection.hours_to_breach is None
    assert projection.projected_breach_at is None


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def _wire_predictive_projection(monkeypatch, *, spent_usd: float) -> None:
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 15.0)
    monkeypatch.setattr(vast_budget.settings, "vast_budget_predictive_alert_horizon_days", 30)
    monkeypatch.setattr(vast_budget.settings, "vast_budget_predictive_alert_cooldown_minutes", 360)
    monkeypatch.setattr(vast_budget, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(vast_budget, "monthly_vast_spend_usd", lambda session, **_kw: spent_usd)


def test_check_predictive_breach_sets_gauge_and_sends_alert(monkeypatch) -> None:
    """Wiring test: projection flows to the Prometheus gauge + Telegram."""
    vast_budget.reset_predictive_alert_state()
    _wire_predictive_projection(monkeypatch, spent_usd=3.0)
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)

    projection = vast_budget._check_predictive_breach(0.5)

    assert projection is not None
    assert projection.will_breach is True
    assert round(metrics.gauge_value(metrics.vast_burn_hours_to_cap), 4) == 24.0
    assert metrics.gauge_value(metrics.vast_burn_projected_breach_timestamp_seconds) > 0
    assert len(alerts) == 1
    assert "burn-rate breach projected" in alerts[0]
    assert "Projected breach:" in alerts[0]


def test_check_predictive_breach_no_alert_when_not_projected(monkeypatch) -> None:
    vast_budget.reset_predictive_alert_state()
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 15.0)
    monkeypatch.setattr(vast_budget.settings, "vast_budget_predictive_alert_horizon_days", 1)
    monkeypatch.setattr(vast_budget, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(vast_budget, "monthly_vast_spend_usd", lambda session, **_kw: 3.0)
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)

    # 12 USD left, burning 0.01/hr -> 1200h to breach; horizon 24h -> no breach.
    projection = vast_budget._check_predictive_breach(0.01)

    assert projection is not None
    assert projection.will_breach is False
    assert alerts == []
    assert metrics.gauge_value(metrics.vast_burn_hours_to_cap) == 1200.0


def test_check_predictive_breach_skipped_when_cap_disabled(monkeypatch) -> None:
    vast_budget.reset_predictive_alert_state()
    monkeypatch.setattr(vast_budget.settings, "vast_monthly_cap_usd", 0.0)
    monkeypatch.setattr(vast_budget, "SessionLocal", lambda: pytest.fail("must not open a session"))
    assert vast_budget._check_predictive_breach(0.5) is None
    assert metrics.gauge_value(metrics.vast_burn_projected_breach_timestamp_seconds) == -1
    assert metrics.gauge_value(metrics.vast_burn_hours_to_cap) == -1


def test_check_predictive_breach_cooldown_suppresses_repeat_alerts(monkeypatch) -> None:
    """A sustained breach raises at most one alert per cooldown window (#355
    review: no per-cycle spam)."""
    vast_budget.reset_predictive_alert_state()
    _wire_predictive_projection(monkeypatch, spent_usd=3.0)
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)

    first = vast_budget._check_predictive_breach(0.5)
    second = vast_budget._check_predictive_breach(0.5)

    assert first is not None and first.will_breach is True
    assert second is not None and second.will_breach is True
    # Same cycle, same projection -> only the transition alert fires.
    assert len(alerts) == 1

    # After the cooldown elapses, a still-breaching projection re-alerts.
    vast_budget._predictive_alert_state["last_alerted_at"] = (
        dt.datetime.now(dt.UTC) - dt.timedelta(minutes=361)
    )
    third = vast_budget._check_predictive_breach(0.5)
    assert third is not None and third.will_breach is True
    assert len(alerts) == 2


def test_check_predictive_breach_resets_when_projection_clears(monkeypatch) -> None:
    """Once the projection clears, hysteresis resets so the next breach
    raises a fresh alert immediately (not gated by the old cooldown)."""
    vast_budget.reset_predictive_alert_state()
    _wire_predictive_projection(monkeypatch, spent_usd=3.0)
    alerts: list[str] = []
    monkeypatch.setattr(vast_budget, "send_admin_alert", alerts.append)

    assert vast_budget._check_predictive_breach(0.5).will_breach is True
    assert len(alerts) == 1

    # Drop the burn rate so the projection clears (1200h > 24h horizon) by
    # shrinking the horizon to 1 day.
    monkeypatch.setattr(vast_budget.settings, "vast_budget_predictive_alert_horizon_days", 1)
    cleared = vast_budget._check_predictive_breach(0.01)
    assert cleared is not None and cleared.will_breach is False
    assert len(alerts) == 1  # clearing does not alert

    # A fresh breach within the old cooldown window raises immediately.
    monkeypatch.setattr(vast_budget.settings, "vast_budget_predictive_alert_horizon_days", 30)
    again = vast_budget._check_predictive_breach(0.5)
    assert again is not None and again.will_breach is True
    assert len(alerts) == 2
