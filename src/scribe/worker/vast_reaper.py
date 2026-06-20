"""Age/cost-based Vast.ai orphan reaper for scribe-labelled whisper instances."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from scribe.config import settings
from scribe.obs import metrics
from scribe.pipeline.whisper_client import VAST_API
from scribe.worker.vast_budget import instance_burn

log = logging.getLogger("scribe.worker.vast_reaper")

SCRIBE_LABEL_MARKER = "-scribe-whisper-"
_LABEL_TS_RE = re.compile(r"-scribe-whisper-(?P<ts>\d{8}T\d{6}Z)")
# Billable Vast states that accrue cost even when not actively running.
_BILLABLE_STATUSES = frozenset({"running", "loading", "starting", "restarting", "stopped"})
# Non-running states that indicate the instance is stuck initializing and
# may never reach `running` (see #355). These are billable but never produce
# transcription output, so they are reaped aggressively.
_STUCK_STATUSES = frozenset({"loading", "starting", "restarting"})


class VastReaperError(RuntimeError):
    pass


def _vast(api_key: str, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 45) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{VAST_API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise VastReaperError(f"Vast API {method} {path}: HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VastReaperError(f"Vast API {method} {path}: {exc}") from exc
    return json.loads(body) if body.strip() else {}


def _instance_label(instance: dict[str, Any]) -> str:
    for key in ("label", "name", "instance_name"):
        value = instance.get(key)
        if value:
            return str(value)
    return ""


def _is_scribe_instance(instance: dict[str, Any]) -> bool:
    return SCRIBE_LABEL_MARKER in _instance_label(instance)


def _is_billable_status(instance: dict[str, Any]) -> bool:
    statuses = {
        str(instance.get("actual_status") or "").strip().lower(),
        str(instance.get("cur_state") or "").strip().lower(),
        str(instance.get("status") or "").strip().lower(),
    }
    return bool(statuses & _BILLABLE_STATUSES)


def _parse_label_timestamp(label: str) -> dt.datetime | None:
    match = _LABEL_TS_RE.search(label)
    if match is None:
        return None
    return dt.datetime.strptime(match.group("ts"), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)


def _parse_start_date(value: Any) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return dt.datetime.fromtimestamp(float(value), tz=dt.UTC)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return dt.datetime.fromtimestamp(float(stripped), tz=dt.UTC)
        except ValueError:
            pass
        try:
            parsed = dt.datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    return None


def _instance_started_at(instance: dict[str, Any]) -> dt.datetime | None:
    return _parse_label_timestamp(_instance_label(instance)) or _parse_start_date(instance.get("start_date"))


def _instance_id(instance: dict[str, Any]) -> int | None:
    raw = instance.get("id") or instance.get("instance_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _instance_usd_per_hour(instance: dict[str, Any]) -> float:
    """Total live $/hr (compute + storage) for an instance."""
    return instance_burn(instance).total_usd_per_hour


def _instance_status(instance: dict[str, Any]) -> str:
    return (
        str(instance.get("actual_status") or instance.get("cur_state") or instance.get("status") or "")
        .strip()
        .lower()
    )


def _is_stuck_non_running(instance: dict[str, Any]) -> bool:
    """True for scribe instances billable but never reached `running`."""
    return _instance_status(instance) in _STUCK_STATUSES


def _is_cost_runaway(
    instance: dict[str, Any],
    *,
    baseline_usd_per_hour: float,
    cost_multiplier: float,
) -> bool:
    """True when live $/hr exceeds `baseline * cost_multiplier` (>0)."""
    if baseline_usd_per_hour <= 0 or cost_multiplier <= 0:
        return False
    threshold = baseline_usd_per_hour * cost_multiplier
    return _instance_usd_per_hour(instance) > threshold


def _is_stale_scribe_instance(
    instance: dict[str, Any],
    *,
    now: dt.datetime,
    max_age: dt.timedelta,
) -> bool:
    if not _is_scribe_instance(instance) or not _is_billable_status(instance):
        return False
    started_at = _instance_started_at(instance)
    if started_at is None:
        return False
    return now - started_at > max_age


def reap_reason_for(
    instance: dict[str, Any],
    *,
    now: dt.datetime,
    max_age: dt.timedelta,
    baseline_usd_per_hour: float,
    cost_multiplier: float,
    stuck_threshold: dt.timedelta,
) -> str | None:
    """Return a reap reason slug ("age" | "cost" | "stuck") or None.

    Pure predicate so tests can exercise the cost/stuck paths without a
    Vast API round-trip. `cost` and `stuck` fire before max_age so a
    runaway/misconfigured offer or a stuck-initializing instance is reaped
    quickly (see #355). A `cost_multiplier` <= 0 disables cost reaping and a
    `stuck_threshold` <= 0 disables stuck reaping; in both cases a still-old
    instance falls through to `age`.
    """
    if not _is_scribe_instance(instance) or not _is_billable_status(instance):
        return None
    if _is_cost_runaway(instance, baseline_usd_per_hour=baseline_usd_per_hour, cost_multiplier=cost_multiplier):
        return "cost"
    if stuck_threshold > dt.timedelta(0) and _is_stuck_non_running(instance):
        started_at = _instance_started_at(instance)
        if started_at is not None and now - started_at > stuck_threshold:
            return "stuck"
    if _is_stale_scribe_instance(instance, now=now, max_age=max_age):
        return "age"
    return None


def reap_vast_orphans(
    *,
    api_key: str | None = None,
    max_age_minutes: int | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Destroy stale/cost-runaway/stuck scribe-labelled Vast instances and
    return attempts made."""
    api_key = (api_key if api_key is not None else settings.vast_api_key).strip()
    if not api_key:
        log.debug("vast orphan reaper skipped: missing API key")
        return 0

    threshold_minutes = max_age_minutes if max_age_minutes is not None else settings.vast_orphan_reaper_max_age_minutes
    max_age = dt.timedelta(minutes=max(1, threshold_minutes))
    stuck_threshold = dt.timedelta(minutes=max(1, settings.vast_orphan_reaper_stuck_minutes))
    baseline = float(settings.vast_budget_baseline_usd_per_hour)
    cost_multiplier = float(settings.vast_orphan_reaper_cost_multiplier)
    current = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    instances = _vast(api_key, "GET", "/instances/", timeout=45).get("instances", [])

    reaped = 0
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        reason = reap_reason_for(
            instance,
            now=current,
            max_age=max_age,
            baseline_usd_per_hour=baseline,
            cost_multiplier=cost_multiplier,
            stuck_threshold=stuck_threshold,
        )
        if reason is None:
            continue
        instance_id = _instance_id(instance)
        if instance_id is None:
            continue
        label = _instance_label(instance)
        started_at = _instance_started_at(instance)
        age_seconds = int((current - started_at).total_seconds()) if started_at else None
        usd_per_hour = round(_instance_usd_per_hour(instance), 6)
        metrics.vast_orphans_destroyed_total.inc()
        reaped += 1
        log.warning(
            "destroying stale Vast scribe instance",
            extra={
                "vast_instance_id": instance_id,
                "vast_label": label,
                "reap_reason": reason,
                "actual_status": instance.get("actual_status"),
                "cur_state": instance.get("cur_state"),
                "age_seconds": age_seconds,
                "max_age_minutes": threshold_minutes,
                "usd_per_hour": usd_per_hour,
                "baseline_usd_per_hour": round(baseline, 6),
                "cost_multiplier": cost_multiplier,
                "stuck_threshold_minutes": settings.vast_orphan_reaper_stuck_minutes,
            },
        )
        try:
            _vast(api_key, "DELETE", f"/instances/{instance_id}/", {}, timeout=45)
        except VastReaperError as exc:
            log.warning(
                "failed to destroy stale Vast scribe instance",
                extra={
                    "vast_instance_id": instance_id,
                    "vast_label": label,
                    "reap_reason": reason,
                    "error": str(exc),
                },
            )
    return reaped


async def run_vast_reaper_loop() -> None:
    interval = max(1, settings.vast_orphan_reaper_interval_seconds)
    while True:
        try:
            await asyncio.to_thread(reap_vast_orphans)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("vast orphan reaper iteration failed")
        await asyncio.sleep(interval)
