"""Inventory integration service.

The workforce service integrates with the authoritative inventory module through
adapters: reserve, issue, install, consume, return, recover. It keeps local
material requirements/usage and device-installation records for reconciliation
and proof. One device cannot be installed on two active services (authoritative
uniqueness is enforced by the inventory adapter; local serial/MAC constraints
are a second line of defence)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ProofError, ValidationError
from ..integrations.base import get_adapter
from ..models import (
    DeviceInstallation,
    MaterialRequirement,
    MaterialUsage,
    WorkOrder,
)
from .audit_service import append_event, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reserve_material(session: Session, tenant_id, work_order_id: uuid.UUID, *, material_code: str,
                     quantity: int, actor: str | None = None, correlation_id: str | None = None) -> MaterialRequirement:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    if quantity <= 0:
        raise ValidationError("quantity must be positive")
    result = get_adapter("inventory").reserve(material_code=material_code, quantity=quantity,
                                              work_order_id=str(work_order.id), actor=actor or "system",
                                              correlation_id=correlation_id or correlation(None))
    if not result.ok:
        raise ProofError(f"inventory reservation failed: {result.error_detail}")
    requirement = session.scalars(select(MaterialRequirement).where(
        MaterialRequirement.work_order_id == work_order.id,
        MaterialRequirement.material_code == material_code)).first()
    if requirement is None:
        requirement = MaterialRequirement(tenant_id=tenant_id, work_order_id=work_order.id,
                                          material_code=material_code, quantity=quantity, unit="UNIT",
                                          status="RESERVED")
        session.add(requirement)
    else:
        requirement.quantity = max(requirement.quantity, quantity)
        requirement.status = "RESERVED"
    session.flush()
    append_event(session, work_order, "work_order.material_reserved",
                 payload={"material_code": material_code, "quantity": quantity}, actor_type="agent",
                 actor_id=actor, correlation_id=correlation_id or work_order.correlation_id)
    return requirement


def record_device_installation(session: Session, tenant_id, work_order_id: uuid.UUID, *, device_type: str,
                               serial_number: str, mac_address: str | None = None,
                               service_subscription_id: str | None = None, technician_id: uuid.UUID | None = None,
                               actor: str | None = None, correlation_id: str | None = None) -> DeviceInstallation:
    # Local uniqueness defence before the authoritative adapter call.
    existing_serial = session.scalars(select(DeviceInstallation).where(
        DeviceInstallation.tenant_id == tenant_id,
        DeviceInstallation.serial_number == serial_number)).first()
    if existing_serial is not None:
        raise ProofError(f"serial {serial_number} already installed on another service")
    from . import proof_service

    return proof_service.install_device(
        session, tenant_id, work_order_id, device_type=device_type, serial_number=serial_number,
        mac_address=mac_address, service_subscription_id=service_subscription_id,
        technician_id=technician_id, actor=actor, correlation_id=correlation_id)


def record_material_usage(session: Session, tenant_id, work_order_id: uuid.UUID, *, material_code: str,
                          quantity: int, usage_type: str = "CONSUMED", technician_id: uuid.UUID | None = None,
                          actor: str | None = None, correlation_id: str | None = None) -> MaterialUsage:
    from . import proof_service

    return proof_service.record_material_usage(
        session, tenant_id, work_order_id, material_code=material_code, quantity=quantity,
        usage_type=usage_type, technician_id=technician_id, actor=actor, correlation_id=correlation_id)


def return_material(session: Session, tenant_id, work_order_id: uuid.UUID, *, material_code: str,
                    quantity: int, technician_id: uuid.UUID | None = None, actor: str | None = None,
                    correlation_id: str | None = None) -> MaterialUsage:
    from . import proof_service

    return proof_service.record_material_usage(
        session, tenant_id, work_order_id, material_code=material_code, quantity=quantity,
        usage_type="RETURNED", technician_id=technician_id, actor=actor, correlation_id=correlation_id)


def requirements_for_work_order(session: Session, tenant_id, work_order_id: uuid.UUID) -> list[MaterialRequirement]:
    return list(session.scalars(select(MaterialRequirement).where(
        MaterialRequirement.work_order_id == work_order_id, MaterialRequirement.tenant_id == tenant_id)))


def usages_for_work_order(session: Session, tenant_id, work_order_id: uuid.UUID) -> list[MaterialUsage]:
    return list(session.scalars(select(MaterialUsage).where(
        MaterialUsage.work_order_id == work_order_id, MaterialUsage.tenant_id == tenant_id)))
