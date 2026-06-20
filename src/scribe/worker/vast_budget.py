"""Vast.ai live-instance burn-rate monitor.

The monitor reads only GET /api/v0/instances/. It does not touch billing,
charges, invoices, payment, or destructive instance endpoints.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Transcript
from scribe.db.session import SessionLocal
from scribe.obs import metrics
from scribe.pipeline.whisper_client import (
    MAX_INSTANCE_SECONDS,
    VAST_API,
    WhisperError,
)

log = logging.getLogger("scribe.vast_budget")

_MONTH_WINDOW_DAYS = 30

# Hysteresis state for the predictive burn-rate alert (#355). The monitor
# loop holds no per-cycle memory, so without a cooldown a sustained breach
# would raise an alert every budget-check cycle. `_predictive_alert_state`
# tracks (last_alerted_at, last_breach) so we only re-alert after the cooldown
# window, and reset once the projection clears.
_predictive_alert_state: dict[str, Any] = {"last_alerted_at": None, "last_breach": False}


def reset_predictive_alert_state() -> None:
    """Clear the predictive-alert hysteresis state (for tests)."""
    _predictive_alert_state["last_alerted_at"] = None
    _predictive_alert_state["last_breach"] = False


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


@dataclass(frozen=True)
class BurnProjection:
    """Projection of rolling 30-day burn against the monthly cap (#355).

    `hours_to_breach` / `projected_breach_at` are None when no breach is
    projected (cap disabled, burn stopped, or the cap is already reached).
    The hard-cap-already-reached case is intentionally *not* a predictive
    breach: `enforce_monthly_cap` raises on it separately."""

    spent_usd: float
    cap_usd: float
    remaining_usd: float
    burn_rate_usd_per_hour: float
    horizon_hours: float
    hours_to_breach: float | None
    projected_breach_at: dt.datetime | None
    will_breach: bool


def monthly_vast_spend_usd(session: Session, *, now: dt.datetime | None = None) -> float:
    """Sum `transcripts.vast_cost` over the last 30 days."""
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=_MONTH_WINDOW_DAYS)
    total = session.scalar(
        select(func.coalesce(func.sum(Transcript.vast_cost), 0.0))
        .where(Transcript.created_at >= cutoff, Transcript.vast_cost.is_not(None))
    )
    return float(total or 0.0)


def in_flight_vast_reserve_usd(session: Session) -> float:
    """Conservative upper bound on Vast cost still owed by jobs currently in
    flight. Each transcribing job is reserved at the max per-job cost cap
    bounded by the wall-clock instance ceiling."""
    in_flight = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status == JobStatus.transcribing)
    ) or 0
    per_job_cap = min(
        float(settings.vast_max_job_cost),
        float(settings.vast_max_price_per_hour) * MAX_INSTANCE_SECONDS / 3600.0,
    )
    return float(in_flight) * per_job_cap


def project_monthly_breach(
    *,
    spent_usd: float,
    cap_usd: float,
    burn_rate_usd_per_hour: float,
    horizon_hours: float,
    now: dt.datetime | None = None,
) -> BurnProjection:
    """Project rolling-30-day burn against the monthly cap at the current
    live burn rate (#355). Pure math so tests can exercise the projection
    without a DB session.

    - When the cap is disabled, burn is stopped, or the cap is already
      reached/exceeded, `will_breach` is False and no projected breach time
      is emitted (the hard-cap path already fires via enforce_monthly_cap).
    - Otherwise `hours_to_breach = remaining / burn_rate` and a breach is
      flagged when it falls within `horizon_hours`.
    """
    current = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    remaining = cap_usd - spent_usd
    if cap_usd <= 0 or burn_rate_usd_per_hour <= 0 or remaining <= 0:
        return BurnProjection(
            spent_usd=float(spent_usd),
            cap_usd=float(cap_usd),
            remaining_usd=float(remaining),
            burn_rate_usd_per_hour=float(burn_rate_usd_per_hour),
            horizon_hours=float(horizon_hours),
            hours_to_breach=None,
            projected_breach_at=None,
            will_breach=False,
        )
    hours_to_breach = remaining / burn_rate_usd_per_hour
    projected_breach_at = current + dt.timedelta(hours=hours_to_breach)
    will_breach = hours_to_breach <= horizon_hours
    return BurnProjection(
        spent_usd=float(spent_usd),
        cap_usd=float(cap_usd),
        remaining_usd=float(remaining),
        burn_rate_usd_per_hour=float(burn_rate_usd_per_hour),
        horizon_hours=float(horizon_hours),
        hours_to_breach=hours_to_breach,
        projected_breach_at=projected_breach_at,
        will_breach=will_breach,
    )


def enforce_monthly_cap(session: Session, *, now: dt.datetime | None = None) -> None:
    """Raise WhisperError if the rolling 30-day Vast spend (including a
    conservative in-flight reservation) is at or above the configured cap."""
    cap = float(settings.vast_monthly_cap_usd)
    if cap <= 0:
        return
    spent = monthly_vast_spend_usd(session, now=now)
    reserved = in_flight_vast_reserve_usd(session)
    if spent + reserved < cap:
        return
    message = (
        f"Vast monthly cap reached: ${spent:.4f} spent + ${reserved:.4f} reserved "
        f">= cap ${cap:.4f} (rolling {_MONTH_WINDOW_DAYS}d). "
        f"Tune SCRIBE_VAST_MONTHLY_CAP_USD to raise."
    )
    log.warning("vast monthly cap reached", extra={
        "spent_usd": round(spent, 4),
        "reserved_usd": round(reserved, 4),
        "cap_usd": cap,
    })
    try:
        send_admin_alert(f"Scribe Vast monthly cap reached\n{message}")
    except Exception:
        log.exception("vast monthly cap admin alert failed")
    raise WhisperError(message)


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
    burned = tuple(instance_burn(instance) for instance in instances)
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
    _check_predictive_breach(check.burn_rate_usd_per_hour)
    return check


def _check_predictive_breach(burn_rate_usd_per_hour: float) -> BurnProjection | None:
    """Project MTD burn against the monthly cap and alert when on track to
    breach within the configured horizon (#355). Emits Prometheus gauges for
    the projected breach time/hours and a Telegram admin alert.

    Hysteresis (`vast_budget_predictive_alert_cooldown_minutes`) prevents a
    sustained breach from spamming an alert every budget-check cycle: we
    alert on the not-breaching -> breaching transition, and at most once per
    cooldown window while the breach persists. The state resets once the
    projection clears so the next breach raises a fresh alert."""
    cap = float(settings.vast_monthly_cap_usd)
    horizon_hours = max(1, settings.vast_budget_predictive_alert_horizon_days) * 24.0
    if cap <= 0:
        _set_projection_gauges(None, None)
        # Cap disabled: clear hysteresis so a future cap re-enable raises fresh.
        _predictive_alert_state["last_alerted_at"] = None
        _predictive_alert_state["last_breach"] = False
        return None
    try:
        with SessionLocal() as session:
            spent = monthly_vast_spend_usd(session)
    except Exception:
        log.exception("vast predictive burn projection failed: DB read error")
        return None
    projection = project_monthly_breach(
        spent_usd=spent,
        cap_usd=cap,
        burn_rate_usd_per_hour=burn_rate_usd_per_hour,
        horizon_hours=horizon_hours,
    )
    _set_projection_gauges(
        projection.hours_to_breach,
        projection.projected_breach_at,
    )
    if projection.will_breach:
        _emit_predictive_alert(projection)
    else:
        # Projection cleared: reset hysteresis so the next breach raises fresh.
        _predictive_alert_state["last_alerted_at"] = None
        _predictive_alert_state["last_breach"] = False
        log.info(
            "vast burn projection sampled",
            extra={
                "spent_usd": round(projection.spent_usd, 6),
                "cap_usd": round(projection.cap_usd, 6),
                "remaining_usd": round(projection.remaining_usd, 6),
                "burn_rate_usd_per_hour": round(projection.burn_rate_usd_per_hour, 6),
                "hours_to_breach": (
                    round(projection.hours_to_breach, 2)
                    if projection.hours_to_breach is not None
                    else None
                ),
            },
        )
    return projection


def _set_projection_gauges(
    hours_to_breach: float | None, projected_breach_at: dt.datetime | None
) -> None:
    if hours_to_breach is not None and projected_breach_at is not None:
        metrics.vast_burn_projected_breach_timestamp_seconds.set(
            projected_breach_at.timestamp()
        )
        metrics.vast_burn_hours_to_cap.set(hours_to_breach)
    else:
        metrics.vast_burn_projected_breach_timestamp_seconds.set(-1)
        metrics.vast_burn_hours_to_cap.set(-1)


def _emit_predictive_alert(projection: BurnProjection) -> None:
    assert projection.projected_breach_at is not None
    assert projection.hours_to_breach is not None
    now = dt.datetime.now(dt.UTC)
    cooldown = dt.timedelta(minutes=max(0, settings.vast_budget_predictive_alert_cooldown_minutes))
    last_alerted = _predictive_alert_state["last_alerted_at"]
    was_breaching = _predictive_alert_state["last_breach"]
    # Alert on the not-breaching -> breaching transition, or once per cooldown
    # window while the breach persists. Otherwise stay quiet (hysteresis).
    if was_breaching and last_alerted is not None and now - last_alerted < cooldown:
        return
    breach_utc = projection.projected_breach_at.strftime("%Y-%m-%d %H:%M UTC")
    message = (
        "Scribe Vast.ai burn-rate breach projected\n"
        f"Spent: ${projection.spent_usd:.4f} / cap ${projection.cap_usd:.4f} "
        f"(rolling {_MONTH_WINDOW_DAYS}d)\n"
        f"Burn rate: ${projection.burn_rate_usd_per_hour:.4f}/hour\n"
        f"Projected breach: {breach_utc} "
        f"(in {projection.hours_to_breach:.1f} hours)\n"
        "Tune SCRIBE_VAST_MONTHLY_CAP_USD or reduce concurrency to slow burn."
    )
    log.warning(
        "vast burn-rate breach projected",
        extra={
            "spent_usd": round(projection.spent_usd, 6),
            "cap_usd": round(projection.cap_usd, 6),
            "burn_rate_usd_per_hour": round(projection.burn_rate_usd_per_hour, 6),
            "hours_to_breach": round(projection.hours_to_breach, 2),
            "projected_breach_at": breach_utc,
        },
    )
    try:
        send_admin_alert(message)
    except Exception:
        log.exception("vast predictive burn admin alert failed")
        return
    _predictive_alert_state["last_alerted_at"] = now
    _predictive_alert_state["last_breach"] = True


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


def instance_burn(instance: dict[str, Any]) -> InstanceBurn:
    """Total live $/hr (compute + storage) billed for an instance.

    Public surface used by both the budget monitor and the orphan reaper
    (#355) so the cost-rate calculation is not duplicated across modules."""
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
