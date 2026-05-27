from __future__ import annotations

import json
import urllib.request

from scribe.obs import metrics
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
