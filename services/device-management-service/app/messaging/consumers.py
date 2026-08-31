"""Idempotent inbound event consumers. Every handler is gated by the consumer
inbox so duplicate deliveries run at most once. Tenant scope comes from the
event envelope (never the payload alone). Handlers only react — they never
mutate another service's data."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import canonical_event_type, consume_once
from ..models import ManagedCpe
from ..services import configuration_service, device_service, diagnostic_service
from ..services import profile_service


def handle_event(session: Session, event: dict, consumer: str = "device-management-handler") -> dict:
    try:
        event_type = canonical_event_type(event.get("event_type", ""))
    except ValueError:
        # Events this service does not subscribe to are acknowledged + ignored.
        return {"handled": True, "action": "ignored"}
    event_id = str(event.get("id") or event.get("event_id") or event.get("correlation_id") or "")
    tenant_id = event.get("tenant_id")
    payload = event.get("payload") or {}
    if not event_id:
        return {"handled": False, "action": "missing_event_id"}
    if not consume_once(session, event_id, consumer):
        return {"handled": False, "action": "duplicate"}
    if not tenant_id:
        return {"handled": False, "action": "missing_tenant"}
    try:
        action = _dispatch(session, event_type, tenant_id, payload, event_id)
    except Exception:  # noqa: BLE001
        session.rollback()
        return {"handled": False, "action": "error"}
    session.commit()
    return {"handled": True, "action": action}


def _dispatch(session: Session, event_type: str, tenant_id, payload: dict, event_id: str) -> str:
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except ValueError:
        return "invalid_tenant"

    if event_type == "order.cpe_provisioning_requested.v1":
        return _provisioning_requested(session, tenant_uuid, payload, event_id)
    if event_type in ("inventory.device_installed.v1", "work_order.device_installed.v1"):
        return _device_installed(session, tenant_uuid, payload)
    if event_type == "inventory.device_recovered.v1":
        return _device_recovered(session, tenant_uuid, payload)
    if event_type in ("service.activated.v1", "service.plan_changed.v1"):
        return _service_changed(session, tenant_uuid, payload)
    if event_type == "ticket.device_diagnostic_requested.v1":
        return _ticket_diagnostic(session, tenant_uuid, payload)
    if event_type == "nms.device_investigation_requested.v1":
        return _nms_investigation(session, tenant_uuid, payload)
    return "ignored"


def _provisioning_requested(session: Session, tenant_id, payload: dict, event_id: str) -> str:
    """OSS asks to provision a CPE for an order: claim/link the device, resolve
    the profile and queue provisioning."""
    serial = payload.get("serial_number") or payload.get("device_serial")
    if not serial:
        return "provisioning:no_serial"
    device = session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.serial_number == serial)).first()
    if device is None:
        return "provisioning:device_not_found"
    device_service.assign_device(
        session, tenant_id, device.id, oss_order_id=payload.get("order_id"),
        customer_id=payload.get("customer_id"),
        service_subscription_id=payload.get("service_subscription_id"),
        service_location_id=payload.get("service_location_id"), actor="oss-consumer",
        correlation_id=event_id)
    profile, version, decision = profile_service.resolve_profile_for_device(
        session, tenant_id, device, correlation_id=event_id)
    if profile is not None and version is not None and device.state in ("ASSIGNED", "PROVISIONING", "ACTIVE"):
        job = configuration_service.create_configuration_job(
            session, tenant_id, device.id, profile_version_id=version.id,
            requested_by="oss-consumer", actor="oss-consumer", correlation_id=event_id)
        configuration_service.approve_job(session, tenant_id, job.id, actor="oss-consumer")
        configuration_service.queue_job(session, tenant_id, job.id, actor="oss-consumer", correlation_id=event_id)
        return f"provisioning:{profile.code}"
    return "provisioning:no_profile"


def _device_installed(session: Session, tenant_id, payload: dict) -> str:
    serial = payload.get("serial_number") or payload.get("device_serial")
    if not serial:
        return "installed:no_serial"
    device = session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.serial_number == serial)).first()
    if device is None:
        return "installed:device_not_found"
    device.inventory_serial = serial
    device.inventory_asset_id = payload.get("asset_id") or payload.get("inventory_id")
    device.work_order_id = payload.get("work_order_id")
    device.oss_order_id = payload.get("order_id")
    if device.state in ("CLAIMED", "INVENTORY_MATCHED"):
        device.state = "INVENTORY_MATCHED"
    session.flush()
    return "installed:linked"


def _device_recovered(session: Session, tenant_id, payload: dict) -> str:
    serial = payload.get("serial_number")
    device = session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.serial_number == serial)).first()
    if device is None:
        return "recovered:device_not_found"
    device.inventory_asset_id = None
    device.inventory_serial = None
    session.flush()
    return "recovered:unlinked"


def _service_changed(session: Session, tenant_id, payload: dict) -> str:
    cpe_id = payload.get("cpe_id")
    if not cpe_id:
        return "service:no_cpe"
    try:
        cpe_uuid = uuid.UUID(str(cpe_id))
    except ValueError:
        return "service:invalid_cpe"
    device = session.get(ManagedCpe, cpe_uuid)
    if device is None or device.tenant_id != tenant_id:
        return "service:device_not_found"
    if payload.get("service_subscription_id"):
        device.service_subscription_id = payload["service_subscription_id"]
    profile, version, _ = profile_service.resolve_profile_for_device(
        session, tenant_id, device, correlation_id=str(cpe_uuid))
    if profile is not None and version is not None:
        job = configuration_service.create_configuration_job(
            session, tenant_id, device.id, profile_version_id=version.id,
            requested_by="service-consumer", actor="service-consumer", correlation_id=str(cpe_uuid))
        configuration_service.queue_job(session, tenant_id, job.id, actor="service-consumer",
                                        correlation_id=str(cpe_uuid))
        return "service:queued"
    return "service:no_profile"


def _ticket_diagnostic(session: Session, tenant_id, payload: dict) -> str:
    serial = payload.get("serial_number")
    diagnostic_type = (payload.get("diagnostic_type") or "PING").upper()
    device = session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.serial_number == serial)).first()
    if device is None:
        return "ticket_diag:device_not_found"
    job = diagnostic_service.create_diagnostic_job(
        session, tenant_id, device.id, diagnostic_type=diagnostic_type,
        input_parameters=payload.get("input_parameters") or {}, requested_by="support",
        support_ticket_id=payload.get("ticket_id"), correlation_id=str(device.id))
    return f"ticket_diag:{job.diagnostic_type}"


def _nms_investigation(session: Session, tenant_id, payload: dict) -> str:
    serial = payload.get("serial_number")
    device = session.scalars(select(ManagedCpe).where(
        ManagedCpe.tenant_id == tenant_id, ManagedCpe.serial_number == serial)).first()
    if device is None:
        return "nms_investigation:device_not_found"
    job = diagnostic_service.create_diagnostic_job(
        session, tenant_id, device.id, diagnostic_type=(payload.get("diagnostic_type") or "WAN_STATUS").upper(),
        input_parameters=payload.get("input_parameters") or {}, requested_by="nms",
        correlation_id=str(device.id))
    return f"nms_investigation:{job.diagnostic_type}"
