"""Idempotent inbound consumers. Every event is gated by the inbox; the tenant
context must be valid and the tenant must exist before any write. Consumers
never inherit stale context across events (context is set per event)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..context import TenantContext, set_context, reset_context
from ..events import canonical_event_type, consume_once
from ..models import Tenant
from ..services import commission_service


def handle_event(session: Session, event: dict, consumer: str = "tenancy-handler") -> dict:
    """Returns {'handled': bool, 'action': str}. Clears tenant context after."""
    token = None
    try:
        event_type = canonical_event_type(event.get("event_type", ""))
    except ValueError:
        return {"handled": True, "action": "ignored"}
    event_id = str(event.get("id") or event.get("event_id") or event.get("correlation_id") or "")
    tenant_raw = event.get("tenant_id")
    payload = event.get("payload") or {}
    if not event_id:
        return {"handled": False, "action": "missing_event_id"}
    if not consume_once(session, event_id, consumer):
        return {"handled": False, "action": "duplicate"}
    if not tenant_raw:
        session.commit()
        return {"handled": False, "action": "missing_tenant"}
    try:
        tenant_uuid = uuid.UUID(str(tenant_raw))
    except (ValueError, TypeError):
        session.commit()
        return {"handled": False, "action": "invalid_tenant"}
    tenant = session.get(Tenant, tenant_uuid)
    if tenant is None or tenant.status not in ("ACTIVE", "RESTRICTED"):
        session.rollback()
        return {"handled": False, "action": "tenant_not_active"}
    ctx = TenantContext(tenant_id=tenant_uuid, db_alias="control", auth_method="internal_event",
                        correlation_id=event.get("correlation_id"))
    token = set_context(ctx)
    try:
        action = _dispatch(session, event_type, tenant_uuid, payload, event_id)
        session.commit()
        return {"handled": True, "action": action}
    except Exception:  # noqa: BLE001
        session.rollback()
        return {"handled": False, "action": "error"}
    finally:
        if token is not None:
            reset_context(token)


def _dispatch(session: Session, event_type: str, tenant_id, payload: dict, event_id: str) -> str:
    if event_type == "billing.payment.captured.v1":
        return _payment_captured(session, tenant_id, payload, event_id)
    if event_type == "billing.payment.refunded.v1":
        return _payment_refunded(session, tenant_id, payload, event_id)
    if event_type == "crm.customer.activated.v1":
        return _customer_activated(session, tenant_id, payload, event_id)
    if event_type == "oss.order.activated.v1":
        return _customer_activated(session, tenant_id, payload, event_id)
    if event_type == "billing.invoice.issued.v1":
        return _invoice_issued(session, tenant_id, payload, event_id)
    return "ignored"


def _partner_for_payload(session: Session, tenant_id, payload: dict):
    partner_id = payload.get("partner_id") or payload.get("franchise_id")
    if not partner_id:
        return None
    from ..models import Partner

    return session.scalars(select(Partner).where(
        Partner.tenant_id == tenant_id, Partner.id == uuid.UUID(str(partner_id)))).first()


def _payment_captured(session: Session, tenant_id, payload: dict, event_id: str) -> str:
    partner = _partner_for_payload(session, tenant_id, payload)
    if partner is None:
        return "earning:no_partner"
    commission_service.recognize_earning(
        session, tenant_id, partner_id=partner.id, source_event_id=event_id,
        source_event_type="billing.payment.captured.v1", basis="PAYMENT_COLLECTION",
        basis_amount=float(payload.get("amount", 0)),
        customer_id=payload.get("customer_id"), payment_ref=payload.get("payment_ref"),
        currency=payload.get("currency", "INR"), actor="billing-consumer", correlation_id=event_id)
    return f"earning:{partner.id}"


def _payment_refunded(session: Session, tenant_id, payload: dict, event_id: str) -> str:
    partner = _partner_for_payload(session, tenant_id, payload)
    if partner is None:
        return "clawback:no_partner"
    from ..models import CommissionEarning

    earning = session.scalars(select(CommissionEarning).where(
        CommissionEarning.tenant_id == tenant_id, CommissionEarning.partner_id == partner.id,
        CommissionEarning.payment_ref == payload.get("payment_ref"))).first()
    if earning is None:
        return "clawback:no_earning"
    commission_service.clawback_earning(
        session, tenant_id, earning.id, amount=None, kind="REFUND",
        source_event_id=event_id, actor="billing-consumer", correlation_id=event_id)
    return "clawback:created"


def _customer_activated(session: Session, tenant_id, payload: dict, event_id: str) -> str:
    partner = _partner_for_payload(session, tenant_id, payload)
    if partner is None:
        return "earning:no_partner"
    commission_service.recognize_earning(
        session, tenant_id, partner_id=partner.id, source_event_id=event_id,
        source_event_type="service.activation", basis="SERVICE_ACTIVATION",
        basis_amount=float(payload.get("activation_fee", 0) or 0),
        customer_id=payload.get("customer_id"), service_id=payload.get("service_id"),
        currency=payload.get("currency", "INR"), actor="crm-consumer", correlation_id=event_id)
    return "earning:activation"


def _invoice_issued(session: Session, tenant_id, payload: dict, event_id: str) -> str:
    partner = _partner_for_payload(session, tenant_id, payload)
    if partner is None:
        return "earning:no_partner"
    commission_service.recognize_earning(
        session, tenant_id, partner_id=partner.id, source_event_id=event_id,
        source_event_type="billing.invoice.issued.v1", basis="INVOICE_AMOUNT",
        basis_amount=float(payload.get("amount", 0) or 0),
        customer_id=payload.get("customer_id"), invoice_ref=payload.get("invoice_id"),
        currency=payload.get("currency", "INR"), actor="billing-consumer", correlation_id=event_id)
    return "earning:invoice"
