"""Idempotent scheduled AAA maintenance tasks; an external scheduler invokes these."""
from datetime import datetime, timedelta, timezone
from os import getenv
from sqlalchemy import select
from sqlalchemy.orm import Session
from .events import declare_topology, publish_outbox
from .commands import RadiusCommandAdapter
from .locks import acquire_nas_lock, release_nas_lock
from .models import ActiveSession, IpLease, Nas, NasCredential, NasDesiredConfiguration, NasJob, NasRadiusAssignment, RadiusCommand, RadiusServer, Tenant
from .nas_lifecycle import job_transition, transition
from .nas_service import apply_nas_configuration, build_adapter, discover_nas, rollback_nas_configuration, run_nas_health_check, test_nas_connection
from .security import decrypt_secret
from .services import correlation, outbox

def detect_stale_sessions(session: Session, stale_after_seconds: int | None = None) -> int:
    seconds = stale_after_seconds or int(getenv("AAA_SESSION_STALE_SECONDS", "900"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    sessions = list(session.scalars(select(ActiveSession).where(ActiveSession.status == "ACTIVE", ActiveSession.last_interim_at.is_not(None), ActiveSession.last_interim_at < cutoff)))
    for item in sessions:
        item.status = "STALE"
        outbox(session, "aaa.session.stale.v1", item.tenant_id, correlation(None), {"session_id": str(item.id), "nas_id": str(item.nas_id)})
    session.commit()
    return len(sessions)

def evaluate_radius_server_health(session: Session, heartbeat_after_seconds: int | None = None) -> int:
    seconds = heartbeat_after_seconds or int(getenv("AAA_RADIUS_HEARTBEAT_SECONDS", "120"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    changed = 0
    for item in session.scalars(select(RadiusServer).where(RadiusServer.enabled.is_(True))):
        health = "healthy" if item.last_heartbeat_at and item.last_heartbeat_at >= cutoff else "unhealthy"
        if item.health != health:
            item.health = health; changed += 1
            outbox(session, "aaa.radius_server.health_changed.v1", None, correlation(None), {"radius_server_id": str(item.id), "health": health})
    session.commit()
    return changed

def cleanup_stale_leases(session: Session) -> int:
    """Release non-reserved leases whose attached session has ended or vanished."""
    released = 0
    for lease in session.scalars(select(IpLease).where(IpLease.reservation.is_(False), IpLease.released_at.is_(None), IpLease.active_session_id.is_not(None))):
        active = session.get(ActiveSession, lease.active_session_id)
        if active is None or active.status == "STOPPED":
            lease.active_session_id = None
            lease.released_at = datetime.now(timezone.utc)
            released += 1
    session.commit()
    return released

def flush_outbox(session: Session) -> int:
    return publish_outbox(session, limit=int(getenv("AAA_OUTBOX_BATCH_SIZE", "100")))

def ensure_event_topology() -> None:
    import asyncio
    asyncio.run(declare_topology())

def _advance_lifecycle(nas: Nas, target: str) -> None:
    try:
        nas.lifecycle_status = transition(nas.lifecycle_status, target)
    except ValueError:
        # Already past the target; keep the richer state.
        pass


def _job_finished(session: Session, job: NasJob, status: str) -> None:
    try:
        job.status = job_transition(job.status, status)
    except ValueError:
        job.status = status


def _load_active_credential(session: Session, nas: Nas) -> NasCredential | None:
    return session.scalar(select(NasCredential).where(NasCredential.nas_id == nas.id, NasCredential.status == "active").order_by(NasCredential.created_at.desc()).limit(1))


def process_nas_job(session: Session, job_id) -> str | None:
    """Process one queued NAS job. Runs under a per-NAS lock so two workers can
    never configure the same NAS simultaneously. Idempotent by design."""
    job = session.get(NasJob, job_id)
    if job is None:
        return None
    if job.status not in {"QUEUED", "PENDING"}:
        return job.status
    nas = session.get(Nas, job.nas_id)
    if nas is None or not nas.enabled:
        _job_finished(session, job, "FAILED")
        job.safe_result = {**job.safe_result, "error": "NAS is disabled or missing"}
        session.commit()
        return "FAILED"

    acquired, owner = acquire_nas_lock(session, nas.id, ttl_seconds=int(getenv("AAA_NAS_LOCK_TTL_SECONDS", "90")))
    if not acquired:
        return "LOCKED"  # leave QUEUED; another worker owns the NAS
    try:
        credential = _load_active_credential(session, nas)
        adapter = build_adapter(nas, credential)
        job.status = "RUNNING"
        session.commit()
        result = _dispatch_job(session, nas, job, adapter)
        _job_finished(session, job, result["status"])
        job.safe_result = {**job.safe_result, "result": result.get("safe_result", {})}
        event = result["event"]
        if result.get("status") == "SUCCEEDED":
            _advance_lifecycle(nas, result.get("lifecycle", nas.lifecycle_status))
            outbox(session, event, nas.tenant_id, job.correlation_id, {"nas_id": str(nas.id), "job_id": str(job.id), "job_type": job.job_type}, job.idempotency_key)
        else:
            outbox(session, event, nas.tenant_id, job.correlation_id, {"nas_id": str(nas.id), "job_id": str(job.id), "error": result.get("error", "job failed")}, job.idempotency_key)
        session.commit()
        return job.status
    except Exception:  # noqa: BLE001 - recover the session and mark the job failed
        session.rollback()
        job = session.get(NasJob, job_id)
        if job is not None:
            try:
                _job_finished(session, job, "FAILED")
            except ValueError:
                job.status = "FAILED"
            job.safe_result = {**job.safe_result, "error": "JOB_EXECUTION_FAILED"}
            session.commit()
        return "FAILED"
    finally:
        try:
            release_nas_lock(session, nas.id, owner)
        except Exception:  # noqa: BLE001 - release is best effort
            session.rollback()


def _dispatch_job(session: Session, nas: Nas, job: NasJob, adapter) -> dict:
    job_type = job.job_type
    if job_type == "connection_test":
        result = test_nas_connection(session, nas, adapter)
        if result.get("ok"):
            _advance_lifecycle(nas, "CONNECTED")
            return {"status": "SUCCEEDED", "event": "nas.connection_test.completed.v1", "lifecycle": "CONNECTED", "safe_result": {"ok": True, "version": result.get("version")}}
        _advance_lifecycle(nas, "FAILED")
        return {"status": "FAILED", "event": "nas.connection_test.failed.v1", "error": result.get("error"), "safe_result": {"ok": False, "error": result.get("error")}}
    if job_type == "discovery":
        _advance_lifecycle(nas, "DISCOVERING")
        try:
            result = discover_nas(session, nas, adapter)
            _advance_lifecycle(nas, "DISCOVERED")
            return {"status": "SUCCEEDED", "event": "nas.discovery.completed.v1", "lifecycle": "DISCOVERED", "safe_result": {"ok": True, "identity": result.get("identity"), "version": result.get("version"), "snapshot_checksum": result.get("snapshot_checksum")}}
        except Exception as error:  # noqa: BLE001 - mapped to safe failure
            nas.failure_reason = "DISCOVERY_FAILED"
            _advance_lifecycle(nas, "FAILED")
            session.commit()
            return {"status": "FAILED", "event": "nas.discovery.failed.v1", "error": "DISCOVERY_FAILED", "safe_result": {}}
    if job_type == "configuration_apply":
        _advance_lifecycle(nas, "CONFIGURING")
        desired, assignments = _load_desired(session, nas)
        if not desired:
            return {"status": "FAILED", "event": "nas.configuration.failed.v1", "error": "no desired configuration", "safe_result": {}}
        tenant = session.get(Tenant, nas.tenant_id)
        tenant_policy = tenant.policy if tenant else {}
        try:
            result = apply_nas_configuration(session, nas, adapter, desired, assignments, tenant_policy)
            if result.get("ok") and result.get("verified"):
                _advance_lifecycle(nas, "VERIFYING")
                _advance_lifecycle(nas, "CONFIGURED")
                return {"status": "SUCCEEDED", "event": "nas.configuration.completed.v1", "lifecycle": "CONFIGURED", "safe_result": {"applied": len(result.get("applied", [])), "verified": True}}
            nas.failure_reason = "VERIFICATION_FAILED"
            session.commit()
            return {"status": "FAILED", "event": "nas.configuration.failed.v1", "error": "VERIFICATION_FAILED", "safe_result": {"verified": False, "differences": result.get("differences", [])}}
        except Exception as error:  # noqa: BLE001 - normalized at the boundary
            nas.failure_reason = "CONFIGURATION_FAILED"
            session.commit()
            return {"status": "FAILED", "event": "nas.configuration.failed.v1", "error": "CONFIGURATION_FAILED", "safe_result": {}}
    if job_type == "configuration_rollback":
        try:
            result = rollback_nas_configuration(session, nas, adapter)
            if result.get("ok"):
                return {"status": "SUCCEEDED", "event": "nas.configuration.rollback_completed.v1", "lifecycle": nas.lifecycle_status, "safe_result": {"applied": len(result.get("applied", []))}}
            return {"status": "FAILED", "event": "nas.configuration.rollback_failed.v1", "error": result.get("error", "ROLLBACK_FAILED"), "safe_result": {}}
        except Exception:  # noqa: BLE001
            return {"status": "FAILED", "event": "nas.configuration.rollback_failed.v1", "error": "ROLLBACK_FAILED", "safe_result": {}}
    if job_type == "health_check":
        desired, assignments = _load_desired(session, nas)
        tenant = session.get(Tenant, nas.tenant_id)
        result = run_nas_health_check(session, nas, adapter, desired, assignments, tenant.policy if tenant else None)
        return {"status": "SUCCEEDED", "event": "nas.health_changed.v1", "lifecycle": nas.lifecycle_status, "safe_result": {"ok": result.get("ok"), "checks": result.get("checks", [])}}
    return {"status": "FAILED", "event": "nas.configuration.failed.v1", "error": "UNKNOWN_JOB_TYPE", "safe_result": {}}


def _load_desired(session: Session, nas: Nas):
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == nas.id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas.id).order_by(NasRadiusAssignment.priority)))
    return (desired.configuration if desired else None), assignments


def queue_nas_job(session: Session, nas_id, job_type: str, idempotency_key: str, correlation_id: str, safe_result: dict | None = None) -> NasJob | None:
    """Create a QUEUED NAS job with idempotency protection and publish the
    request event through the outbox. Returns None when a duplicate exists."""
    existing = session.scalar(select(NasJob).where(NasJob.nas_id == nas_id, NasJob.idempotency_key == idempotency_key))
    if existing:
        return None
    nas = session.get(Nas, nas_id)
    job = NasJob(nas_id=nas_id, job_type=job_type, status="QUEUED", idempotency_key=idempotency_key, correlation_id=correlation_id, safe_result=safe_result or {})
    session.add(job)
    session.flush()
    event_map = {"connection_test": "nas.connection_test.requested.v1", "discovery": "nas.discovery.requested.v1", "configuration_apply": "nas.configuration.requested.v1", "configuration_rollback": "nas.configuration.rollback_requested.v1", "health_check": "nas.health_changed.v1"}
    outbox(session, event_map.get(job_type, "nas.configuration.requested.v1"), nas.tenant_id if nas else None, correlation_id, {"nas_id": str(nas_id), "job_id": str(job.id), "job_type": job_type}, idempotency_key)
    return job


def run_nas_health_checks(session: Session, limit: int = 10) -> int:
    """Schedule bounded health checks for enabled NAS devices. Intervals are
    enforced by the scheduler; routers are never polled more often than the
    configured minimum interval."""
    minimum_interval = int(getenv("AAA_NAS_HEALTH_INTERVAL_SECONDS", "300"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=minimum_interval)
    candidates = list(session.scalars(select(Nas).where(Nas.enabled.is_(True), (Nas.last_connected_at.is_(None)) | (Nas.last_connected_at < cutoff)).order_by(Nas.last_connected_at.asc().nullsfirst()).limit(limit)))
    for nas in candidates:
        job = queue_nas_job(session, nas.id, "health_check", f"health:{nas.id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}", correlation(None), {})
        if job is not None:
            session.commit()
    session.commit()
    return len(candidates)


def process_radius_command(session: Session, adapter: RadiusCommandAdapter, command_id=None) -> str | None:
    """Execute one queued command. A queue acknowledgement is never a session stop."""
    maximum_attempts = int(getenv("AAA_COMMAND_MAX_ATTEMPTS", "3"))
    statement = select(RadiusCommand).where(RadiusCommand.status == "QUEUED", RadiusCommand.attempts < maximum_attempts)
    if command_id is not None: statement = statement.where(RadiusCommand.id == command_id)
    command = session.scalar(statement.order_by(RadiusCommand.created_at).limit(1))
    if not command: return None
    command.status = "SENDING"; command.attempts += 1; session.commit()
    nas = session.get(Nas, command.nas_id)
    if not nas or not nas.enabled or not nas.secret_ciphertext:
        result = None
    else:
        try:
            secret = decrypt_secret(nas.secret_ciphertext)
            result = adapter.send_disconnect(nas.source_ip, nas.coa_port, secret, command.attributes) if command.command_type == "DISCONNECT" else adapter.send_coa(nas.source_ip, nas.coa_port, secret, command.attributes)
        except Exception:
            result = None
    status = result.status if result else "FAILED"
    command.status, command.result = status, {"detail": result.detail if result else "NAS secret or delivery unavailable"}
    event = "aaa.disconnect.completed.v1" if command.command_type == "DISCONNECT" and status == "ACKNOWLEDGED" else "aaa.coa.completed.v1" if command.command_type == "COA" and status == "ACKNOWLEDGED" else "aaa.disconnect.failed.v1" if command.command_type == "DISCONNECT" else "aaa.coa.failed.v1"
    outbox(session, event, command.tenant_id, command.correlation_id, {"command_id": str(command.id), "status": status}, command.idempotency_key)
    if command.session_id:
        active = session.get(ActiveSession, command.session_id)
        if active:
            active.status = "DISCONNECT_ACKNOWLEDGED" if command.command_type == "DISCONNECT" and status == "ACKNOWLEDGED" else "DISCONNECT_TIMED_OUT" if status == "TIMED_OUT" else active.status
    session.commit()
    return status
