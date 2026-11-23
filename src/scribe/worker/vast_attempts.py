"""Durable per-job market rental costs and the one-shot rerun reservation."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from scribe.db.models import Job, JobStageEvent, JobStatus
from scribe.pipeline.whisper_client import VastAttempt, VastLedger

_RETRY_RESERVED = "vast_rerun_reserved"
_ATTEMPT_PREFIX = "vast_attempt_"


def _attempt_event(session: Session, job_id: int, instance_id: int) -> JobStageEvent | None:
    return session.scalar(
        select(JobStageEvent)
        .where(JobStageEvent.job_id == job_id, JobStageEvent.stage == f"{_ATTEMPT_PREFIX}{instance_id}")
        .with_for_update()
    )


def _finish_reservation(session: Session, job_id: int, now: datetime) -> None:
    session.execute(update(JobStageEvent).where(
        JobStageEvent.job_id == job_id, JobStageEvent.stage == _RETRY_RESERVED,
        JobStageEvent.finished_at.is_(None),
    ).values(finished_at=now))


class JobVastAttempts:
    def __init__(self, job_id: int, session_factory) -> None:
        self.job_id = job_id
        self.session_factory = session_factory

    def load(self) -> VastLedger:
        with self.session_factory() as session:
            events = session.scalars(
                select(JobStageEvent).where(
                    JobStageEvent.job_id == self.job_id,
                    (JobStageEvent.stage == _RETRY_RESERVED)
                    | JobStageEvent.stage.startswith(_ATTEMPT_PREFIX, autoescape=True),
                )
            )
            ledger = VastLedger()
            for event in events:
                if event.stage == _RETRY_RESERVED:
                    ledger.retry_reserved = True
                else:
                    ledger.attempts.append(VastAttempt(
                        started_at=event.started_at, finished_at=event.finished_at, **event.details,
                    ))
            return ledger

    def reserve_retry(self, label: str, offer_id: int, host_id: int | None, hourly_usd: float) -> bool:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            reserved = session.scalar(
                insert(JobStageEvent)
                .values(job_id=self.job_id, stage=_RETRY_RESERVED, started_at=now, details={
                    "label": label, "offer_id": offer_id, "host_id": host_id, "hourly_usd": hourly_usd,
                })
                .on_conflict_do_nothing(constraint="uq_job_stage_events_job_stage")
                .returning(JobStageEvent.id)
            )
            session.commit()
            return reserved is not None

    def reconcile_retry(self) -> None:
        from scribe.worker.loop import reconcile_vast_reruns

        with self.session_factory() as session:
            for reservation, _ in pending_reruns(session, recovering=False, job_id=self.job_id):
                reservation.details = reservation.details | {"create_failed": True}
            session.commit()
            reconcile_vast_reruns(session, job_id=self.job_id)

    def record(self, attempt: VastAttempt) -> None:
        details = asdict(attempt)
        started_at = details.pop("started_at")
        finished_at = details.pop("finished_at")
        with self.session_factory() as session:
            if attempt.outcome == "running":
                job = session.get(Job, self.job_id, with_for_update=True)
                job.vast_instance_id = attempt.instance_id
                job.destroy_failed_at = None
                if attempt.number == 2:
                    _finish_reservation(session, self.job_id, datetime.now(UTC))

            event = _attempt_event(session, self.job_id, attempt.instance_id)
            if event is None:
                event = JobStageEvent(
                    job_id=self.job_id, stage=f"{_ATTEMPT_PREFIX}{attempt.instance_id}", started_at=started_at,
                )
                session.add(event)
            elif event.details["destroyed"] and not attempt.destroyed:
                # The wall-clock watchdog can finish teardown from a separate
                # thread. Keep its confirmation and account for the full rental.
                details["destroyed"] = True
                details["estimated_cost_usd"] = event.details["estimated_cost_usd"]
                finished_at = event.finished_at
            event.finished_at = finished_at
            event.details = details
            session.commit()


def confirm_destroy(session: Session, job_id: int, instance_id: int) -> None:
    """Close a rental that was torn down by the watchdog or the cleanup sweep."""
    event = _attempt_event(session, job_id, instance_id)
    if event is None or event.details["destroyed"]:
        return
    now = datetime.now(UTC)
    details = dict(event.details)
    if details["outcome"] == "running":
        details["outcome"] = "interrupted"
        details["exclude_host"] = True
    elapsed = max(0.0, (now - event.started_at).total_seconds())
    details["estimated_cost_usd"] = max(details["estimated_cost_usd"], details["hourly_usd"] * elapsed / 3600)
    details["destroyed"] = True
    event.details = details
    event.finished_at = now
    if details["number"] == 2:
        _finish_reservation(session, job_id, now)


def pending_reruns(session: Session, *, recovering: bool, job_id: int | None = None):
    rows = select(JobStageEvent, Job).join(Job).where(
        JobStageEvent.stage == _RETRY_RESERVED, JobStageEvent.finished_at.is_(None),
    )
    if job_id is not None:
        rows = rows.where(Job.id == job_id)
    elif not recovering:
        grace = datetime.now(UTC) - timedelta(seconds=30)
        rows = rows.where(Job.status.in_((JobStatus.done, JobStatus.failed)) | (
            JobStageEvent.details["create_failed"].as_boolean().is_(True) & (JobStageEvent.started_at < grace)
        ))
    return session.execute(rows.with_for_update(of=Job, skip_locked=True)).all()


def adopt_untracked_reruns(session: Session, pending, instances: list[dict]) -> list[Job]:
    """Save matched instance IDs and billing metadata before making any DELETE."""
    by_label = {item.get("label"): item.get("id") or item.get("instance_id") for item in instances}
    adopted = []
    for reservation, job in pending:
        intent = reservation.details
        instance_id = intent.get("instance_id") or by_label.get(intent["label"])
        if instance_id is None:
            continue
        instance_id = int(instance_id)
        if _attempt_event(session, job.id, instance_id) is None:
            session.add(JobStageEvent(
                job_id=job.id, stage=f"{_ATTEMPT_PREFIX}{instance_id}", started_at=reservation.started_at,
                details={k: intent[k] for k in ("offer_id", "host_id", "hourly_usd")} | {
                    "number": 2, "instance_id": instance_id, "outcome": "running",
                    "destroyed": False, "exclude_host": False, "estimated_cost_usd": 0.0,
                },
            ))
        job.vast_instance_id = instance_id
        reservation.details = intent | {"instance_id": instance_id}
        adopted.append(job)
    session.commit()
    return adopted
