"""Offline-first synchronization rules for technician mobile commands.

Client commands carry a UUID, idempotency key, device/session reference, local
timestamp and expected entity version. Rules:

- A technician cannot complete a cancelled work order.
- An offline check-in is accepted only if the assignment was valid at the
  recorded time.
- Offline material use cannot exceed confirmed technician stock without review.
- A stale offline status must not overwrite a newer server state (version check).
- Duplicate command retries are rejected idempotently.
- Duplicate upload retries never create duplicate proof records."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import OfflineCommandError, ValidationError
from ..enums import TERMINAL_WORK_ORDER_STATES
from ..models import OfflineCommand, WorkOrder


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_offline_command(session: Session, tenant_id, *, client_command_id: str, work_order_id,
                             command: str, device_ref: str | None, entity_version: int | None,
                             local_timestamp: datetime | None) -> OfflineCommand:
    """Idempotency + version + state validation for an offline command."""
    existing = session.scalars(
        select(OfflineCommand).where(OfflineCommand.tenant_id == tenant_id,
                                     OfflineCommand.client_command_id == client_command_id)).first()
    if existing is not None:
        # Duplicate retry: return the recorded outcome, never re-execute.
        if existing.status in ("PROCESSED", "REJECTED"):
            return existing
        raise OfflineCommandError("duplicate offline command already recorded", code="offline_duplicate")

    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise ValidationError("work order not found")
    if work_order.status in TERMINAL_WORK_ORDER_STATES and command not in ("VIEW", "SYNC"):
        raise OfflineCommandError(f"cannot run {command} on a terminal work order", code="terminal_work_order")

    if entity_version is not None:
        if entity_version < work_order.aggregate_version:
            raise OfflineCommandError(
                f"stale command (client version {entity_version} < server {work_order.aggregate_version})",
                code="version_conflict")
        if entity_version > work_order.aggregate_version:
            raise OfflineCommandError("command references a future version", code="version_conflict")

    command_row = OfflineCommand(
        tenant_id=tenant_id,
        client_command_id=client_command_id,
        device_ref=device_ref,
        work_order_id=work_order_id,
        command=command,
        payload={},
        local_timestamp=local_timestamp,
        status="RECEIVED",
        entity_version=work_order.aggregate_version,
    )
    session.add(command_row)
    session.flush()
    return command_row


def mark_offline_processed(command: OfflineCommand, result: dict | None = None) -> None:
    command.status = "PROCESSED"
    command.result = result or {}
    command.attempts += 1


def mark_offline_conflict(command: OfflineCommand, reason: str) -> None:
    command.status = "CONFLICT"
    command.conflict_reason = reason
    command.attempts += 1
