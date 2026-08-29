"""Idempotent inbound event consumers for the workforce service.

Every handler is gated by the consumer inbox so duplicate deliveries run at
most once. Tenant scope comes from the event envelope (never the payload
alone). Handlers only react — the workforce service never mutates another
service's data; it creates/links its own work orders."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import canonical_event_type, consume_once
from ..models import MaterialRequirement, WorkOrder
from ..services import appointment_service, workorder_service
from . import consumed_helpers  # noqa: F401  (registry)


def handle_event(session: Session, event: dict, consumer: str = "workforce-handler") -> dict:
    event_type = canonical_event_type(event.get("event_type", ""))
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
        action = _dispatch(session, event_type, tenant_id, payload, event_id=event_id)
    except Exception:  # noqa: BLE001
        session.rollback()
        return {"handled": False, "action": "error"}
    session.commit()
    return {"handled": True, "action": action}


def _dispatch(session: Session, event_type: str, tenant_id, payload: dict, *, event_id: str | None = None) -> str:
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except ValueError:
        return "invalid_tenant"

    if event_type == "oss.order.field_work_required.v1":
        order_type = payload.get("order_type", "NEW_CONNECTION")
        wo_type = _wo_type_for_order(order_type)
        wo = workorder_service.create_work_order(
            session, tenant_uuid, work_order_type=wo_type, customer_id=payload.get("customer_id"),
            service_subscription_id=payload.get("service_subscription_id"),
            service_location_id=payload.get("service_location_id"),
            oss_order_id=payload.get("order_id"), oss_order_number=payload.get("order_number"),
            priority=payload.get("priority", "P3_MEDIUM"),
            latitude=payload.get("latitude"), longitude=payload.get("longitude"),
            source_channel="OSS", actor="oss-consumer", correlation_id=event_id)
        return f"created:{wo.work_order_number}"
    if event_type == "support.ticket.field_visit_requested.v1":
        wo = workorder_service.create_work_order(
            session, tenant_uuid, work_order_type="FAULT_REPAIR", customer_id=payload.get("customer_id"),
            service_subscription_id=payload.get("service_subscription_id"),
            service_location_id=payload.get("service_location_id"),
            support_ticket_id=payload.get("ticket_id"), support_ticket_number=payload.get("ticket_number"),
            nms_incident_id=payload.get("incident_id"), priority="P3_MEDIUM",
            source_channel="SUPPORT", actor="support-consumer", correlation_id=event_id)
        return f"created:{wo.work_order_number}"
    if event_type == "nms.repair_required.v1":
        wo = workorder_service.create_work_order(
            session, tenant_uuid, work_order_type="FAULT_REPAIR", customer_id=payload.get("customer_id"),
            service_location_id=payload.get("service_location_id"),
            nms_incident_id=payload.get("incident_id"), priority=payload.get("priority", "P2_HIGH"),
            source_channel="NMS", actor="nms-consumer", correlation_id=event_id)
        return f"created:{wo.work_order_number}"
    if event_type == "inventory.reservation_confirmed.v1":
        return _mark_reservation_confirmed(session, tenant_uuid, payload)
    if event_type in ("oss.service.activation_completed.v1", "oss.service.activation_failed.v1"):
        return _advance_remote_action(session, tenant_uuid, payload, ok=event_type.endswith("completed.v1"))
    if event_type == "crm.customer.updated.v1":
        return _refresh_customer(session, tenant_uuid, payload)
    if event_type == "workforce.appointment.customer_confirmed.v1":
        return _confirm_appointment(session, tenant_uuid, payload)
    return "ignored"


def _wo_type_for_order(order_type: str) -> str:
    mapping = {
        "NEW_CONNECTION": "NEW_INSTALLATION",
        "SERVICE_RELOCATION": "SERVICE_RELOCATION",
        "SERVICE_TERMINATION": "SERVICE_DISCONNECTION",
        "SERVICE_DISCONNECTION": "SERVICE_DISCONNECTION",
        "DEVICE_REPLACEMENT": "ONT_REPLACEMENT",
        "DEVICE_PICKUP": "DEVICE_PICKUP",
    }
    return mapping.get(order_type, "NEW_INSTALLATION")


def _mark_reservation_confirmed(session: Session, tenant_id, payload: dict) -> str:
    work_order_id = payload.get("work_order_id")
    material_code = payload.get("material_code")
    if not work_order_id or not material_code:
        return "reservation:missing_refs"
    requirement = session.scalars(select(MaterialRequirement).where(
        MaterialRequirement.tenant_id == tenant_id,
        MaterialRequirement.work_order_id == uuid.UUID(str(work_order_id)),
        MaterialRequirement.material_code == material_code)).first()
    if requirement is not None:
        requirement.status = "ISSUED"
    return "reservation:updated"


def _advance_remote_action(session: Session, tenant_id, payload: dict, *, ok: bool) -> str:
    oss_order_id = payload.get("order_id")
    count = 0
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == tenant_id)
    if oss_order_id:
        stmt = stmt.where(WorkOrder.oss_order_id == oss_order_id)
    else:
        return "activation:no_order"
    for wo in list(session.scalars(stmt)):
        if wo.status == "AWAITING_REMOTE_ACTION":
            try:
                workorder_service.resume_work_order(session, tenant_id, wo.id, actor="oss-consumer")
            except Exception:  # noqa: BLE001
                pass
            count += 1
    return f"activation:{count}"


def _refresh_customer(session: Session, tenant_id, payload: dict) -> str:
    customer_id = payload.get("customer_id")
    if not customer_id:
        return "customer:no_id"
    orders = list(session.scalars(select(WorkOrder).where(
        WorkOrder.tenant_id == tenant_id, WorkOrder.customer_id == customer_id)))
    for wo in orders:
        if payload.get("name"):
            wo.customer_name = payload["name"]
    return f"customer:{len(orders)}"


def _confirm_appointment(session: Session, tenant_id, payload: dict) -> str:
    appointment_id = payload.get("appointment_id")
    if not appointment_id:
        return "appointment:no_id"
    try:
        appointment_service.confirm(session, tenant_id, uuid.UUID(str(appointment_id)), actor="customer-portal")
        return "appointment:confirmed"
    except Exception:  # noqa: BLE001
        return "appointment:error"
