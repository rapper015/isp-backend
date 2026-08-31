"""Capability-aware remote diagnostics with governed jobs and normalized
results. A timeout caused by an offline device is distinguished from a
completed failed test."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import diagnostics as diag_rules
from ..domain.exceptions import DiagnosticError, NotFoundError, StateTransitionError
from ..enums import DIAGNOSTIC_TYPES
from ..integrations.acs import get_acs_client
from ..integrations.base import get_adapter
from ..models import DiagnosticJob, DiagnosticResult, ManagedCpe, SupportedDiagnostic
from ..state_machine import diagnostic_job_transition
from . import device_service
from .audit_service import append_event, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_job_or_404(session: Session, tenant_id, job_id: uuid.UUID) -> DiagnosticJob:
    job = session.get(DiagnosticJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise NotFoundError("diagnostic job not found")
    return job


def supported_diagnostics(session: Session, tenant_id, cpe_id: uuid.UUID) -> list[str]:
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    if device.model_variant_id is None:
        return []
    return [row.diagnostic for row in session.scalars(
        select(SupportedDiagnostic).where(SupportedDiagnostic.model_variant_id == device.model_variant_id))]


def create_diagnostic_job(session: Session, tenant_id, cpe_id: uuid.UUID, *, diagnostic_type: str,
                          input_parameters: dict | None = None, requested_by: str = "system",
                          support_ticket_id: str | None = None, idempotency_key: str | None = None,
                          actor: str | None = None,
                          correlation_id: str | None = None) -> DiagnosticJob:
    request_id = correlation(correlation_id)
    if idempotency_key:
        existing = session.scalars(select(DiagnosticJob).where(
            DiagnosticJob.tenant_id == tenant_id, DiagnosticJob.idempotency_key == idempotency_key)).first()
        if existing is not None:
            return existing
    diagnostic_type = diagnostic_type.upper()
    if diagnostic_type not in DIAGNOSTIC_TYPES:
        raise DiagnosticError(f"invalid diagnostic type {diagnostic_type!r}")
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    available = supported_diagnostics(session, tenant_id, cpe_id)
    job = DiagnosticJob(
        tenant_id=tenant_id, cpe_id=cpe_id, diagnostic_type=diagnostic_type, state="REQUESTED",
        input_parameters=input_parameters or {}, requested_by=requested_by,
        support_ticket_id=support_ticket_id, customer_id=device.customer_id,
        service_subscription_id=device.service_subscription_id, idempotency_key=idempotency_key,
        correlation_id=request_id)
    session.add(job)
    session.flush()
    if diagnostic_type not in available:
        _transition(job, "UNSUPPORTED")
        job.failure_code = "DIAGNOSTIC_UNSUPPORTED"
    else:
        _transition(job, "VALIDATING")
    append_event(session, device, "diagnostic.requested", payload={"job_id": str(job.id),
                                                                   "diagnostic_type": diagnostic_type},
                 actor_type="agent", actor_id=actor or requested_by, correlation_id=request_id)
    session.flush()
    return job


def _transition(job: DiagnosticJob, target: str) -> None:
    try:
        diagnostic_job_transition(job.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    job.state = target


def run_diagnostic(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system",
                   correlation_id: str | None = None) -> DiagnosticJob:
    job = get_job_or_404(session, tenant_id, job_id)
    if job.state == "UNSUPPORTED":
        raise DiagnosticError("diagnostic is unsupported for this device")
    if job.state in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"):
        raise DiagnosticError(f"diagnostic already terminal ({job.state})")
    device = session.get(ManagedCpe, job.cpe_id)
    if job.state == "VALIDATING":
        _transition(job, "QUEUED")
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    outcome = client.trigger_connection_request(device.acs_device_id)
    if outcome in ("UNREACHABLE", "TIMED_OUT"):
        _transition(job, "WAITING_FOR_DEVICE")
        job.failure_code = "DEVICE_OFFLINE"
    else:
        _transition(job, "RUNNING")
        job.started_at = _now()
        task_id = client.create_task(device.acs_device_id, job.diagnostic_type, job.input_parameters)["task_id"]
        job.genieacs_task_id = task_id
    session.flush()
    return job


def complete_diagnostic(session: Session, tenant_id, job_id: uuid.UUID, *, raw: dict | None = None,
                        offline: bool = False, failed: bool = False, fault_code: str | None = None,
                        actor: str = "system", correlation_id: str | None = None) -> DiagnosticJob:
    job = get_job_or_404(session, tenant_id, job_id)
    device = session.get(ManagedCpe, job.cpe_id)
    if offline:
        # Device never came online — distinguish from a completed failed test.
        _transition(job, "TIMED_OUT")
        job.failure_code = "DEVICE_OFFLINE"
        job.completed_at = _now()
        session.add(DiagnosticResult(tenant_id=tenant_id, job_id=job.id, cpe_id=device.id,
                                     normalized_result={"offline": True}, units={}, evaluation="UNKNOWN",
                                     offline=True, fault_code="DEVICE_OFFLINE"))
        append_event(session, device, "diagnostic.device_offline", payload={"job_id": str(job.id)},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        session.flush()
        return job
    if failed:
        _transition(job, "FAILED")
        job.failure_code = fault_code or "DIAGNOSTIC_FAILED"
        job.completed_at = _now()
        session.add(DiagnosticResult(tenant_id=tenant_id, job_id=job.id, cpe_id=device.id,
                                     normalized_result=diag_rules.normalize_diagnostic_result(job.diagnostic_type, raw),
                                     units={}, evaluation="FAIL", fault_code=fault_code or "DIAGNOSTIC_FAILED"))
        append_event(session, device, "diagnostic.failed", payload={"job_id": str(job.id),
                                                                    "code": job.failure_code},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        session.flush()
        return job
    if job.state in ("RUNNING", "COLLECTING_RESULTS"):
        _transition(job, "COLLECTING_RESULTS")
    normalized = diag_rules.normalize_diagnostic_result(job.diagnostic_type, raw or {})
    evaluation = diag_rules.evaluate_diagnostic(job.diagnostic_type, normalized)
    _transition(job, "SUCCEEDED")
    job.completed_at = _now()
    session.add(DiagnosticResult(tenant_id=tenant_id, job_id=job.id, cpe_id=device.id,
                                 normalized_result=normalized, units={}, evaluation=evaluation))
    if job.support_ticket_id:
        get_adapter("support").link_diagnostic(ticket_id=job.support_ticket_id, diagnostic_job_id=str(job.id),
                                               summary={"type": job.diagnostic_type, "evaluation": evaluation},
                                               actor=actor, correlation_id=correlation_id or job.correlation_id)
    append_event(session, device, "diagnostic.completed", payload={"job_id": str(job.id),
                                                                   "evaluation": evaluation},
                 actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
    outbox(session, "cpe.diagnostic_completed.v1", tenant_id, correlation_id or job.correlation_id,
           {"cpe_id": str(job.cpe_id), "job_id": str(job.id), "diagnostic_type": job.diagnostic_type,
            "evaluation": evaluation})
    session.flush()
    return job


def cancel_diagnostic(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system") -> DiagnosticJob:
    job = get_job_or_404(session, tenant_id, job_id)
    if job.state in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "UNSUPPORTED"):
        raise DiagnosticError(f"cannot cancel a {job.state} diagnostic")
    _transition(job, "CANCELLED")
    session.flush()
    return job
