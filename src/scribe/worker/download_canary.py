"""Nightly yt-dlp download canary.

Exercises the real download path (metadata + audio stream) against a known
stable public video on a fixed cadence. Updates Prometheus gauges and fires
an admin Telegram alert when the canary goes red so we get an early signal
when a yt-dlp release or a YouTube-side change breaks the pinned downloader.

Runbook (linked from the alert text):
docs/runbooks/download-canary.md
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from scribe.alerts import send_admin_alert
from scribe.config import settings
from scribe.obs import metrics
from scribe.pipeline.downloader import DownloadError, download_audio

log = logging.getLogger("scribe.worker.download_canary")


def run_download_canary(url: str | None = None) -> bool:
    """Run one canary download. Returns True on success, False on failure.

    Never raises — a broken canary path must not crash the process. All
    Prometheus + alert side effects happen here so callers (the loop, an
    on-demand ops endpoint, a test) get identical behaviour.
    """
    target = (url or settings.download_canary_url).strip()
    if not target:
        log.warning("download canary skipped: empty url")
        return False

    with tempfile.TemporaryDirectory(prefix="scribe-canary-") as tmp:
        try:
            result = download_audio(
                target,
                Path(tmp),
                pot_base_url=settings.bgutil_pot_base_url or None,
            )
        except DownloadError as exc:
            _record_failure(target, str(exc))
            return False
        except Exception as exc:  # defensive — unexpected subprocess/IO failures
            _record_failure(target, f"unexpected error: {exc}")
            return False

        if not result.audio_path.is_file() or result.audio_path.stat().st_size <= 0:
            _record_failure(target, "yt-dlp returned an empty/missing audio file")
            return False

    metrics.download_canary_runs_total.labels(outcome="ok").inc()
    metrics.download_canary_status.set(1)
    metrics.download_canary_last_success_timestamp.set(time.time())
    log.info("download canary green", extra={"canary_url": target, "title": result.title})
    return True


def _record_failure(url: str, detail: str) -> None:
    metrics.download_canary_runs_total.labels(outcome="fail").inc()
    metrics.download_canary_status.set(0)
    log.error("download canary RED", extra={"canary_url": url, "detail": detail[:500]})
    runbook = settings.download_canary_runbook_url.strip()
    alert = (
        "scribe yt-dlp download canary RED\n"
        f"url: {url}\n"
        f"error: {detail[:500]}\n"
    )
    if runbook:
        alert += f"runbook: {runbook}\n"
    send_admin_alert(alert)


async def run_download_canary_loop() -> None:
    """Periodic canary loop. Started from the FastAPI lifespan."""
    if not settings.download_canary_enabled:
        log.info("download canary disabled (SCRIBE_DOWNLOAD_CANARY_ENABLED=false)")
        return
    initial_delay = max(0, settings.download_canary_initial_delay_seconds)
    interval = max(60, settings.download_canary_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await asyncio.to_thread(run_download_canary)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("download canary iteration crashed")
        await asyncio.sleep(interval)
