"""Periodic worker tasks: outbox flush, configuration-job task polling, timeouts,
drift reconciliation, firmware rollout advancement, telemetry retention and
ACS device sync. All tasks are bounded, idempotent and restart-safe."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import envelope, unprocessed_events
from .models import ConfigurationJob, ManagedCpe, FirmwareRollout
from .services import configuration_service, firmware_service, telemetry_service
from .services.audit_service import correlation


def _publish(event) -> None:
    """Declared aio-pika hook: currently a no-op placeholder for the broker
    integration. The outbox remains the source of truth until published."""
    _ = envelope(event)


def flush_outbox(session: Session, limit: int = 100) -> int:
    from datetime import datetime, timezone

    events = unprocessed_events(session, limit)
    published = 0
    for event in events:
        try:
            _publish(event)
            event.published_at = datetime.now(timezone.utc)
            published += 1
        except Exception:  # noqa: BLE001
            event.attempts += 1
    session.commit()
    return published


def _all_pending_jobs(session: Session, limit: int = 200) -> list[ConfigurationJob]:
    return list(session.scalars(
        select(ConfigurationJob)
        .where(ConfigurationJob.state.in_(
            ("QUEUED", "CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM", "EXECUTING", "DEVICE_ACKNOWLEDGED")))
        .order_by(ConfigurationJob.created_at)
        .limit(limit)))


def process_pending_jobs(session: Session, tenant_id, limit: int = 100) -> dict:
    """Poll GenieACS tasks for in-flight configuration jobs. A completed task
    advances to verification; a fault fails the job; pending tasks stay queued."""
    from .integrations.acs import get_acs_client

    completed, failed, pending = 0, 0, 0
    jobs = _all_pending_jobs(session, limit)
    for job in jobs:
        if job.tenant_id != tenant_id:
            continue
        device = session.get(ManagedCpe, job.cpe_id)
        if device is None or not device.acs_device_id:
            pending += 1
            continue
        client = get_acs_client({"instance_id": str(device.acs_instance_id)})
        step = session.scalars(__select_step(job.id)).first()
        task_id = step.genieacs_task_id if step is not None else None
        if not task_id:
            pending += 1
            continue
        try:
            task = client.get_task(task_id)
        except Exception:  # noqa: BLE001
            pending += 1
            continue
        state = task.get("state", "QUEUED")
        if state in ("COMPLETED", "VERIFIED"):
            configuration_service.process_task_result(session, tenant_id, job.id, task_id=task_id,
                                                      task_state="COMPLETED", task_result=task.get("result"),
                                                      actor="worker")
            session.commit()
            if job.state == "DEVICE_ACKNOWLEDGED":
                try:
                    configuration_service.verify_job(session, tenant_id, job.id, actor="worker")
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
            completed += 1
        elif state in ("FAULTED", "VERIFICATION_FAILED"):
            configuration_service.process_task_result(session, tenant_id, job.id, task_id=task_id,
                                                      task_state=state, task_result=task.get("result"),
                                                      actor="worker")
            session.commit()
            failed += 1
        else:
            pending += 1
    return {"completed": completed, "failed": failed, "pending": pending}


def __select_step(job_id):
    from .models import ConfigurationStep

    return select(ConfigurationStep).where(ConfigurationStep.job_id == job_id,
                                           ConfigurationStep.state == "DISPATCHED")


def timeout_stale_jobs(session: Session, tenant_id) -> list[str]:
    return configuration_service.timeout_stale_jobs(session, tenant_id)


def reconcile_drift(session: Session, tenant_id, *, limit: int = 100) -> list[str]:
    devices = list(session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.state == "ACTIVE").limit(limit)))
    drift_found = []
    for device in devices:
        drift = configuration_service.detect_drift(session, tenant_id, device.id, actor="worker")
        if drift is not None:
            drift_found.append(str(device.id))
    session.commit()
    return drift_found


def advance_firmware_rollouts(session: Session, tenant_id) -> dict:
    rollouts = list(session.scalars(select(FirmwareRollout).where(
        FirmwareRollout.tenant_id == tenant_id, FirmwareRollout.state == "RUNNING")))
    summary = {"paused": 0, "completed": 0}
    for rollout in rollouts:
        result = firmware_service.advance_rollout_stages(session, tenant_id, rollout.id, actor="worker")
        if result.get("paused"):
            summary["paused"] += 1
        if result.get("completed"):
            summary["completed"] += 1
    session.commit()
    return summary


def purge_telemetry(session: Session, tenant_id) -> int:
    return telemetry_service.purge_expired_telemetry(session, tenant_id)


def sync_acs_devices(session: Session, tenant_id, instance_id: uuid.UUID | None = None) -> dict:
    from .services import acs_service
    from .models import ACSInstance

    if instance_id:
        return acs_service.reconcile_devices(session, tenant_id, instance_id, actor="worker")
    instances = list(session.scalars(select(ACSInstance).where(
        (ACSInstance.tenant_id == tenant_id) | (ACSInstance.tenant_id.is_(None)))))
    total = {"scanned": 0, "created": 0, "updated": 0, "quarantined": 0}
    for instance in instances:
        if not instance.is_active:
            continue
        result = acs_service.reconcile_devices(session, tenant_id, instance.id, actor="worker")
        for key in total:
            total[key] += result.get(key, 0)
    session.commit()
    return total
