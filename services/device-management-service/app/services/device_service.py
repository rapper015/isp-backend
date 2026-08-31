"""Managed-CPE service: discovery from ACS, tenant resolution, secure claiming,
assignment, transfer, decommission and identity refresh.

Unknown or ambiguous devices stay quarantined. A device owned by one tenant can
never be claimed by another without an authorized transfer workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import identity as identity_rules
from ..domain.exceptions import (
    AmbiguousOwnershipError,
    DeviceClaimError,
    DuplicateError,
    NotFoundError,
    TenantIsolationError,
    ValidationError,
)
from ..enums import TENANT_RESOLUTION_METHODS, TENANT_RESOLUTION_RESULTS
from ..integrations.base import get_adapter
from ..integrations.acs import get_acs_client
from ..models import ACSDeviceBinding, CpeOnboarding, CpeOwnershipHistory, ManagedCpe, Tenant
from ..state_machine import device_transition
from . import catalog_service
from .audit_service import append_event, audit, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_device_or_404(session: Session, tenant_id, cpe_id: uuid.UUID) -> ManagedCpe:
    device = session.get(ManagedCpe, cpe_id)
    if device is None or device.tenant_id != tenant_id:
        raise NotFoundError("managed device not found")
    return device


def find_by_identity(session: Session, *, oui: str, product_class: str | None, serial: str) -> ManagedCpe | None:
    oui, product_class, serial = identity_rules.acs_identity_key(oui, product_class, serial)
    return session.scalars(
        select(ManagedCpe).where(ManagedCpe.oui == oui, ManagedCpe.product_class == (product_class or ""),
                                 ManagedCpe.serial_number == serial)).first()


def discover_from_acs(session: Session, acs_instance_id: uuid.UUID, *, acs_device_id: str,
                      requested_tenant_id: uuid.UUID | None = None, actor: str = "system",
                      correlation_id: str | None = None) -> ManagedCpe:
    """Pull a device record from ACS, normalize identity, create/update the
    managed CPE and resolve tenant ownership. Unknown devices are quarantined."""
    request_id = correlation(correlation_id)
    client = get_acs_client({"instance_id": str(acs_instance_id)})
    record = client.get_device(acs_device_id)
    if record is None:
        raise NotFoundError("device not present in ACS")
    oui = identity_rules.normalize_oui(record.get("oui"))
    serial = identity_rules.normalize_serial(record.get("serialNumber"))
    product_class = (record.get("productClass") or "").strip().upper()
    if not oui or not serial:
        raise ValidationError("device record lacks OUI/serial identity")

    existing = find_by_identity(session, oui=oui, product_class=product_class or None, serial=serial)
    if existing is not None:
        return _touch_existing(session, existing, acs_instance_id, acs_device_id, actor, request_id)

    tenant = None
    if requested_tenant_id is not None:
        tenant = session.get(Tenant, requested_tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")

    device = ManagedCpe(
        tenant_id=tenant.id if tenant else None,
        oui=oui, product_class=product_class or None, serial_number=serial,
        manufacturer=record.get("manufacturer"), model_name=record.get("modelName"),
        hardware_version=record.get("hardwareVersion"),
        acs_device_id=acs_device_id, acs_instance_id=acs_instance_id,
        state="DISCOVERED", operational_status="UNKNOWN",
        first_inform_at=_now(), last_inform_at=_now(),
        correlation_id=request_id,
    )
    session.add(device)
    session.flush()
    if tenant is not None:
        session.add(ACSDeviceBinding(tenant_id=tenant.id, instance_id=acs_instance_id, cpe_id=device.id,
                                     acs_device_id=acs_device_id, created_by=actor))
        device.tenant_id = tenant.id
        device.state = "IDENTIFIED"
    else:
        device.state = "QUARANTINED"
        _resolve_tenant_auto(session, device, actor, request_id)
    append_event(session, device, "cpe.discovered",
                 payload={"acs_device_id": acs_device_id, "oui": oui, "serial": serial}, actor_type="system",
                 actor_id=actor, correlation_id=request_id)
    outbox(session, "cpe.discovered.v1", device.tenant_id, request_id,
           {"cpe_id": str(device.id), "acs_device_id": acs_device_id, "serial_number": serial,
            "oui": oui, "product_class": product_class})
    session.flush()
    return device


def _touch_existing(session: Session, device: ManagedCpe, acs_instance_id, acs_device_id, actor, request_id) -> ManagedCpe:
    device.last_inform_at = _now()
    if device.acs_instance_id is None:
        device.acs_instance_id = acs_instance_id
    if device.acs_device_id is None:
        device.acs_device_id = acs_device_id
    if device.state == "OFFLINE":
        device_transition(device.state, "ACTIVE")
        device.state = "ACTIVE"
    append_event(session, device, "cpe.inform_received", payload={"acs_device_id": acs_device_id},
                 actor_type="system", actor_id=actor, correlation_id=request_id)
    session.flush()
    return device


def _resolve_tenant_auto(session: Session, device: ManagedCpe, actor: str, correlation_id: str) -> None:
    """Attempt automatic tenant resolution from pre-registered inventory serial."""
    result = get_adapter("inventory").find_asset_by_serial(serial=device.serial_number)
    if result.ok:
        asset = result.output
        audit(session, None, "device.tenant_resolved", "managed_cpe", str(device.id), actor=actor,
              payload={"method": "PREREGISTERED_SERIAL", "asset_id": asset.get("id")},
              correlation_id=correlation_id)
        # Asset serial known but tenant ownership still needs explicit claim.
        device.state = "IDENTIFIED"
    else:
        device.state = "QUARANTINED"
    session.flush()


def resolve_tenant(session: Session, device: ManagedCpe, *, method: str, evidence: str | None = None,
                   actor: str = "system", correlation_id: str | None = None,
                   claimed_tenant_id: uuid.UUID | None = None) -> str:
    """Explicit tenant resolution. Returns the resolution result. On MATCHED the
    device is placed in CLAIM_PENDING."""
    request_id = correlation(correlation_id)
    method = method.upper()
    if method not in TENANT_RESOLUTION_METHODS:
        raise ValidationError(f"invalid tenant resolution method {method!r}")
    conflicting = []
    matched = False
    confidence = None
    if method == "PREREGISTERED_SERIAL":
        result = get_adapter("inventory").find_asset_by_serial(serial=device.serial_number)
        matched = result.ok
        confidence = 1.0 if matched else None
    elif method == "TECHNICIAN_INSTALLATION":
        matched = bool(device.work_order_id) or bool(evidence)
        confidence = 0.9 if matched else None
    elif method == "OSS_ORDER_RESERVATION":
        matched = bool(device.oss_order_id) or bool(evidence)
        confidence = 0.9 if matched else None
    elif method in ("ADMIN_CLAIM", "ONBOARDING_TOKEN"):
        matched = claimed_tenant_id is not None
        confidence = 1.0 if matched else None
    elif method in ("ACS_ENDPOINT", "CIRCUIT_SERVICE_MAPPING"):
        matched = False
        confidence = 0.5
    outcome = identity_rules.resolve_outcome(confidence, matched, bool(conflicting))
    record = CpeOnboarding(tenant_id=device.tenant_id, cpe_id=device.id, resolution_method=method,
                           evidence=evidence, result=outcome, confidence=confidence, actor=actor,
                           conflicting_matches=conflicting, resolved_tenant_id=claimed_tenant_id,
                           correlation_id=request_id)
    session.add(record)
    session.flush()
    if outcome == "MATCHED" and claimed_tenant_id is not None:
        device.tenant_id = claimed_tenant_id
        device.state = "CLAIM_PENDING"
    elif outcome == "AMBIGUOUS":
        device.state = "QUARANTINED"
    elif outcome == "UNKNOWN":
        device.state = "QUARANTINED"
    append_event(session, device, "cpe.tenant_resolved",
                 payload={"method": method, "result": outcome, "confidence": confidence},
                 actor_type="agent" if actor != "system" else "system", actor_id=actor, correlation_id=request_id)
    session.flush()
    return outcome


def claim_device(session: Session, tenant_id: uuid.UUID, cpe_id: uuid.UUID, *, method: str,
                 evidence: str | None = None, actor: str = "system", correlation_id: str | None = None,
                 allowed_result: str = "MATCHED") -> ManagedCpe:
    """Claim a device after valid ownership is established. A device already
    owned by another tenant is rejected unless transferred first."""
    request_id = correlation(correlation_id)
    device = session.get(ManagedCpe, cpe_id)
    if device is None:
        raise NotFoundError("managed device not found")
    if device.tenant_id is not None and device.tenant_id != tenant_id:
        raise TenantIsolationError("device is owned by another tenant; an authorized transfer is required")
    outcome = resolve_tenant(session, device, method=method, evidence=evidence, actor=actor,
                             correlation_id=request_id, claimed_tenant_id=tenant_id)
    if outcome != allowed_result:
        raise AmbiguousOwnershipError(f"device could not be claimed: resolution {outcome}")
    device.tenant_id = tenant_id
    device.state = "CLAIMED"
    device.claimed_by = actor
    device.claimed_at = _now()
    session.add(CpeOwnershipHistory(cpe_id=device.id, from_tenant_id=None, to_tenant_id=tenant_id,
                                    transfer_type="CLAIM", reason=evidence, actor=actor))
    append_event(session, device, "cpe.claimed", payload={"method": method}, actor_type="agent",
                 actor_id=actor, correlation_id=request_id)
    outbox(session, "cpe.claimed.v1", tenant_id, request_id,
           {"cpe_id": str(device.id), "serial_number": device.serial_number, "method": method})
    session.flush()
    return device


def assign_device(session: Session, tenant_id: uuid.UUID, cpe_id: uuid.UUID, *, customer_id: str | None = None,
                  service_subscription_id: str | None = None, service_location_id: str | None = None,
                  oss_order_id: str | None = None, work_order_id: str | None = None,
                  inventory_serial: str | None = None, inventory_asset_id: str | None = None,
                  actor: str = "system", correlation_id: str | None = None) -> ManagedCpe:
    request_id = correlation(correlation_id)
    device = get_device_or_404(session, tenant_id, cpe_id)
    if device.state not in ("CLAIMED", "INVENTORY_MATCHED", "ASSIGNED", "ACTIVE", "PROVISIONING"):
        raise DeviceClaimError(f"cannot assign device in state {device.state}")
    if customer_id:
        device.customer_id = customer_id
    if service_subscription_id:
        device.service_subscription_id = service_subscription_id
    if service_location_id:
        device.service_location_id = service_location_id
    if oss_order_id:
        device.oss_order_id = oss_order_id
    if work_order_id:
        device.work_order_id = work_order_id
    if inventory_serial:
        device.inventory_serial = inventory_serial
        device.inventory_asset_id = inventory_asset_id
    if device.state in ("CLAIMED", "INVENTORY_MATCHED"):
        device.state = "ASSIGNED"
    append_event(session, device, "cpe.assigned",
                 payload={"customer_id": customer_id, "service_subscription_id": service_subscription_id,
                          "oss_order_id": oss_order_id, "work_order_id": work_order_id},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    outbox(session, "cpe.assigned.v1", tenant_id, request_id,
           {"cpe_id": str(device.id), "serial_number": device.serial_number,
            "service_subscription_id": service_subscription_id})
    session.flush()
    return device


def transfer_device(session: Session, from_tenant_id: uuid.UUID, to_tenant_id: uuid.UUID, cpe_id: uuid.UUID,
                    *, reason: str, actor: str = "system", correlation_id: str | None = None) -> ManagedCpe:
    request_id = correlation(correlation_id)
    if not reason or not reason.strip():
        raise ValidationError("transfer requires a reason")
    device = get_device_or_404(session, from_tenant_id, cpe_id)
    if to_tenant_id == from_tenant_id:
        raise ValidationError("source and destination tenants are the same")
    session.add(CpeOwnershipHistory(cpe_id=device.id, from_tenant_id=from_tenant_id, to_tenant_id=to_tenant_id,
                                    transfer_type="TRANSFER", reason=reason, actor=actor))
    device.tenant_id = to_tenant_id
    device.state = "CLAIMED"
    append_event(session, device, "cpe.transferred", payload={"from_tenant": str(from_tenant_id),
                                                               "to_tenant": str(to_tenant_id), "reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    audit(session, from_tenant_id, "device.transferred", "managed_cpe", str(device.id), actor=actor,
          reason=reason, payload={"to_tenant": str(to_tenant_id)}, correlation_id=request_id)
    session.flush()
    return device


def decommission_device(session: Session, tenant_id: uuid.UUID, cpe_id: uuid.UUID, *, reason: str,
                        actor: str = "system", correlation_id: str | None = None) -> ManagedCpe:
    request_id = correlation(correlation_id)
    device = get_device_or_404(session, tenant_id, cpe_id)
    if not reason or not reason.strip():
        raise ValidationError("decommission requires a reason")
    if device.state == "DECOMMISSIONED":
        raise DuplicateError("device already decommissioned")
    device_transition(device.state, "DECOMMISSIONED")
    device.state = "DECOMMISSIONED"
    append_event(session, device, "cpe.decommissioned", payload={"reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    outbox(session, "cpe.decommissioned.v1", tenant_id, request_id,
           {"cpe_id": str(device.id), "serial_number": device.serial_number, "reason": reason})
    audit(session, tenant_id, "device.decommissioned", "managed_cpe", str(device.id), actor=actor,
          reason=reason, correlation_id=request_id)
    session.flush()
    return device


def mark_online(session: Session, tenant_id: uuid.UUID, cpe_id: uuid.UUID, *, actor: str = "system",
                correlation_id: str | None = None) -> ManagedCpe:
    device = get_device_or_404(session, tenant_id, cpe_id)
    was_offline = not device.online
    device.online = True
    device.operational_status = "ONLINE"
    device.last_inform_at = _now()
    if device.state == "OFFLINE":
        device.state = "ACTIVE"
    if was_offline:
        append_event(session, device, "cpe.online", payload={}, actor_type="system", actor_id=actor,
                     correlation_id=correlation_id or device.correlation_id)
        outbox(session, "cpe.online.v1", tenant_id, correlation_id or device.correlation_id,
               {"cpe_id": str(device.id)})
    session.flush()
    return device


def mark_offline(session: Session, tenant_id: uuid.UUID, cpe_id: uuid.UUID, *, actor: str = "system",
                 correlation_id: str | None = None) -> ManagedCpe:
    device = get_device_or_404(session, tenant_id, cpe_id)
    was_online = device.online
    device.online = False
    device.operational_status = "OFFLINE"
    if was_online:
        append_event(session, device, "cpe.offline", payload={}, actor_type="system", actor_id=actor,
                     correlation_id=correlation_id or device.correlation_id)
        outbox(session, "cpe.offline.v1", tenant_id, correlation_id or device.correlation_id,
               {"cpe_id": str(device.id)})
    session.flush()
    return device
