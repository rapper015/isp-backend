"""Offline-first synchronization for technician mobile commands.

Commands carry client UUIDs and are processed with idempotency, ordered
processing where required, conflict detection and retry-safe behaviour. A stale
offline status never overwrites a newer server state. Duplicate retries never
duplicate proof records."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.exceptions import OfflineCommandError, WorkforceError
from ..domain import offline as offline_rules
from ..models import OfflineCommand, WorkOrder


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_offline_commands(session: Session, tenant_id, *, device_ref: str, commands: list[dict],
                             actor: str = "system") -> dict:
    """Process an offline command batch in arrival order. Returns per-command
    results; each command is validated for idempotency + version conflicts."""
    results = []
    for raw in commands:
        results.append(_process_one(session, tenant_id, device_ref=device_ref, raw=raw, actor=actor))
    session.commit()
    return {"results": results, "device_ref": device_ref}


def _process_one(session: Session, tenant_id, *, device_ref: str, raw: dict, actor: str) -> dict:
    client_command_id = raw.get("client_command_id") or uuid.uuid4().hex
    command = raw.get("command", "")
    work_order_id = raw.get("work_order_id")
    payload = raw.get("payload", {}) or {}
    entity_version = raw.get("entity_version")
    local_timestamp = raw.get("local_timestamp")
    if isinstance(local_timestamp, str) and local_timestamp:
        from datetime import datetime as _dt

        try:
            local_timestamp = _dt.fromisoformat(local_timestamp.replace("Z", "+00:00"))
        except ValueError:
            local_timestamp = None
    record = None

    try:
        if work_order_id:
            work_order_id = uuid.UUID(str(work_order_id))
            record = offline_rules.validate_offline_command(
                session, tenant_id, client_command_id=client_command_id, work_order_id=work_order_id,
                command=command, device_ref=device_ref, entity_version=entity_version,
                local_timestamp=local_timestamp)
            if record.status in ("PROCESSED", "REJECTED"):
                # Idempotent retry: return the recorded outcome, never re-execute.
                result = record.result or {}
                return {"client_command_id": client_command_id, "command": command,
                        "status": record.status, "result": result}
            outcome = _dispatch_command(session, tenant_id, command, work_order_id, payload, actor, device_ref)
            offline_rules.mark_offline_processed(record, outcome)
            record.result = outcome
            return {"client_command_id": client_command_id, "command": command, "status": "PROCESSED", "result": outcome}
        raise OfflineCommandError("work_order_id is required for offline commands", code="missing_work_order")
    except WorkforceError as error:
        try:
            if work_order_id and record is not None:
                record.status = "REJECTED"
                record.result = {"code": getattr(error, "code", "error"), "detail": error.message}
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
        return {"client_command_id": client_command_id, "command": command, "status": "REJECTED",
                "code": getattr(error, "code", "error"), "detail": error.message}


def _dispatch_command(session: Session, tenant_id, command: str, work_order_id: uuid.UUID, payload: dict,
                      actor: str, device_ref: str) -> dict:
    from . import inventory_service, visit_service, workorder_service

    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise OfflineCommandError("work order not found", code="not_found")
    technician_id = work_order.assigned_technician_id
    if technician_id is None:
        raise OfflineCommandError("work order is not assigned", code="unassigned")

    offline_payload = {**payload, "offline": True, "device_timestamp": payload.get("local_timestamp")}
    if command == "check_in":
        wo = workorder_service.check_in_work_order(
            session, tenant_id, work_order_id, technician_id=technician_id, payload=offline_payload,
            actor=actor, correlation_id=payload.get("correlation_id"), device_ref=device_ref)
        return {"status": "ARRIVED"}
    if command == "check_out":
        wo = workorder_service.check_out_work_order(
            session, tenant_id, work_order_id, technician_id=technician_id, payload=offline_payload,
            actor=actor, correlation_id=payload.get("correlation_id"), device_ref=device_ref)
        return {"status": "CHECKED_OUT"}
    if command == "start_work":
        workorder_service.start_work(session, tenant_id, work_order_id, actor=actor)
        return {"status": "IN_PROGRESS"}
    if command == "record_blocker":
        workorder_service.record_blocker(session, tenant_id, work_order_id,
                                         blocker_type=payload.get("blocker_type", "OTHER"),
                                         reason=payload.get("reason", ""), actor=actor)
        return {"status": "BLOCKED"}
    if command == "material_use":
        usage = inventory_service.record_material_usage(
            session, tenant_id, work_order_id, material_code=payload.get("material_code", ""),
            quantity=int(payload.get("quantity", 1)), usage_type=payload.get("usage_type", "CONSUMED"),
            technician_id=technician_id, actor=actor, correlation_id=payload.get("correlation_id"))
        return {"material_usage_id": str(usage.id)}
    if command == "install_device":
        installation = inventory_service.record_device_installation(
            session, tenant_id, work_order_id, device_type=payload.get("device_type", "ONT"),
            serial_number=payload.get("serial_number", ""), mac_address=payload.get("mac_address"),
            service_subscription_id=work_order.service_subscription_id, technician_id=technician_id,
            actor=actor, correlation_id=payload.get("correlation_id"))
        return {"device_installation_id": str(installation.id)}
    if command == "finish_execution":
        workorder_service.finish_execution(session, tenant_id, work_order_id, actor=actor)
        return {"status": "EXECUTION_COMPLETED"}
    raise OfflineCommandError(f"unsupported offline command {command!r}", code="unsupported")
