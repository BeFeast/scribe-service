"""Vast.ai live-instance burn-rate monitor.

The monitor reads only GET /api/v0/instances/. It does not touch billing,
charges, invoices, payment, or destructive instance endpoints.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.obs import metrics
from scribe.pipeline.whisper_client import VAST_API

log = logging.getLogger("scribe.vast_budget")


@dataclass(frozen=True)
class InstanceBurn:
    id: str
    label: str
    status: str
    compute_usd_per_hour: float
    storage_usd_per_hour: float
    total_usd_per_hour: float


@dataclass(frozen=True)
class BudgetCheck:
    burn_rate_usd_per_hour: float
    baseline_usd_per_hour: float
    alert_multiplier: float
    threshold_usd_per_hour: float
    is_anomaly: bool
    instances: tuple[InstanceBurn, ...]


def fetch_instances(api_key: str, *, timeout: int = 45) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        f"{VAST_API}/instances/",
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vast API GET /instances/: HTTP {exc.code}: {detail}") from exc

    instances = payload.get("instances", [])
    if isinstance(instances, dict):
        instances = [instances]
    if not isinstance(instances, list):
        raise RuntimeError("Vast API GET /instances/ returned malformed instances")
    return [item for item in instances if isinstance(item, dict)]


def build_budget_check(
    instances: list[dict[str, Any]],
    *,
    baseline_usd_per_hour: float,
    alert_multiplier: float,
) -> BudgetCheck:
    burned = tuple(_instance_burn(instance) for instance in instances)
    burn_rate = sum(item.total_usd_per_hour for item in burned)
    threshold = baseline_usd_per_hour * alert_multiplier
    return BudgetCheck(
        burn_rate_usd_per_hour=burn_rate,
        baseline_usd_per_hour=baseline_usd_per_hour,
        alert_multiplier=alert_multiplier,
        threshold_usd_per_hour=threshold,
        is_anomaly=threshold > 0 and burn_rate > threshold,
        instances=burned,
    )


def check_vast_budget() -> BudgetCheck | None:
    api_key = settings.vast_api_key.strip()
    if not api_key:
        log.warning("vast budget check skipped: SCRIBE_VAST_API_KEY is not set")
        return None

    check = build_budget_check(
        fetch_instances(api_key),
        baseline_usd_per_hour=settings.vast_budget_baseline_usd_per_hour,
        alert_multiplier=settings.vast_budget_alert_multiplier,
    )
    metrics.vast_burn_rate_usd_per_hour.set(check.burn_rate_usd_per_hour)
    if check.is_anomaly:
        _emit_anomaly(check)
    else:
        log.info(
            "vast burn rate sampled",
            extra={
                "burn_rate_usd_per_hour": round(check.burn_rate_usd_per_hour, 6),
                "threshold_usd_per_hour": round(check.threshold_usd_per_hour, 6),
                "instance_count": len(check.instances),
            },
        )
    return check


def start_budget_monitor(interval_seconds: int | None = None) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    interval = max(60, interval_seconds or settings.vast_budget_check_interval_seconds)
    thread = threading.Thread(
        target=_run_monitor,
        args=(stop, interval),
        name="scribe-vast-budget",
        daemon=True,
    )
    thread.start()
    return thread, stop


def _run_monitor(stop: threading.Event, interval_seconds: int) -> None:
    while not stop.is_set():
        try:
            check_vast_budget()
        except Exception:
            log.exception("vast budget check failed")
        stop.wait(interval_seconds)


def _emit_anomaly(check: BudgetCheck) -> None:
    top = sorted(check.instances, key=lambda item: item.total_usd_per_hour, reverse=True)[:5]
    payload = {
        "burn_rate_usd_per_hour": round(check.burn_rate_usd_per_hour, 6),
        "threshold_usd_per_hour": round(check.threshold_usd_per_hour, 6),
        "baseline_usd_per_hour": check.baseline_usd_per_hour,
        "alert_multiplier": check.alert_multiplier,
        "instance_count": len(check.instances),
        "top_instances": [
            {
                "id": item.id,
                "label": item.label,
                "status": item.status,
                "compute_usd_per_hour": round(item.compute_usd_per_hour, 6),
                "storage_usd_per_hour": round(item.storage_usd_per_hour, 6),
                "total_usd_per_hour": round(item.total_usd_per_hour, 6),
            }
            for item in top
        ],
    }
    log.warning("vast burn rate anomaly", extra={"vast_budget": payload})
    send_admin_alert(_format_admin_alert(check, top))


def _format_admin_alert(check: BudgetCheck, top: list[InstanceBurn]) -> str:
    lines = [
        "Scribe Vast.ai burn-rate anomaly",
        f"Current: ${check.burn_rate_usd_per_hour:.4f}/hour",
        f"Threshold: ${check.threshold_usd_per_hour:.4f}/hour "
        f"({check.baseline_usd_per_hour:.4f} x {check.alert_multiplier:g})",
        f"Instances: {len(check.instances)}",
    ]
    if top:
        lines.append("Top instances:")
        lines.extend(
            f"- {item.id} {item.label or '(no label)'} {item.status or 'unknown'} "
            f"${item.total_usd_per_hour:.4f}/hour"
            for item in top
        )
    return "\n".join(lines)


def _instance_burn(instance: dict[str, Any]) -> InstanceBurn:
    storage = _first_float(
        instance,
        ("storage_total_cost",),
        ("search", "diskHour"),
        ("instance", "diskHour"),
        ("storage_dph",),
    )
    compute = _first_float(
        instance,
        ("dph_base",),
        ("search", "gpuCostPerHour"),
        ("machine", "gpuCostPerHour"),
    )
    if compute is None:
        total = _first_float(
            instance,
            ("dph_total",),
            ("search", "totalHour"),
            ("search", "discountedTotalPerHour"),
            ("instance", "totalHour"),
        )
        if total is None:
            compute = 0.0
            total = storage or 0.0
        else:
            compute = max(total - (storage or 0.0), 0.0)
    else:
        total = compute + (storage or 0.0)

    return InstanceBurn(
        id=str(instance.get("id") or ""),
        label=str(instance.get("label") or ""),
        status=str(instance.get("actual_status") or instance.get("cur_state") or ""),
        compute_usd_per_hour=compute,
        storage_usd_per_hour=storage or 0.0,
        total_usd_per_hour=total,
    )


def _first_float(root: dict[str, Any], *paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value: Any = root
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
