"""Controlled support actions.

Support agents may request governed operational actions; the support service
NEVER executes arbitrary RouterOS commands, edits RADIUS/FreeRADIUS
configuration, changes financial ledgers, allocates IPs or modifies unmanaged
network configuration. Every action is routed through the authoritative service
adapter, uses idempotency keys + correlation IDs, and disruptive actions
require preview + confirmation + approval."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import ActionError, DuplicateError, NotFoundError, ValidationError
from ..enums import DISRUPTIVE_ACTION_TYPES, SUPPORT_ACTION_TYPES
from ..integrations.base import get_adapter
from ..models import SupportAction, Ticket
from ..services.audit_service import append_event, audit, correlation, outbox
from . import diagnostic_service, ticket_service

# action_type -> (disruptive, requires_authorization, adapter, method)
_ACTION_MAP = {
    "REFRESH_SUBSCRIBER_CONTEXT": (False, False, "oss", "get_subscriber_context"),
    "RE_RUN_DIAGNOSTICS": (False, False, None, None),
    "REAPPLY_SESSION_POLICY": (True, True, "network", "reapply_policy"),
    "DISCONNECT_REAUTHORIZE": (True, True, "aaa", "disconnect_and_reauth"),
    "REQUEST_COA": (True, True, "aaa", "request_coa"),
    "REQUEST_AAA_RECONCILIATION": (False, False, "aaa", "request_reconciliation"),
    "NAS_REACHABILITY_CHECK": (False, False, "aaa", "nas_reachability"),
    "IP_ASSIGNMENT_RECONCILIATION": (True, True, "ipam", "reconcile_assignment"),
    "RETRY_PROVISIONING_STEP": (False, False, "oss", "retry_order_step"),
    "CREATE_OSS_ORDER": (False, False, "oss", "create_order"),
    "CREATE_WORKFORCE_JOB": (False, False, "workforce", "create_job"),
    "REQUEST_BILLING_REVIEW": (False, False, "bss", "request_billing_review"),
    "REQUEST_PAYMENT_RECONCILIATION": (False, False, "bss", "reconcile_payment"),
    "LINK_OUTAGE": (False, False, None, None),
    "UNLINK_OUTAGE": (False, False, None, None),
}

_ACTION_PREVIEW = {
    "REFRESH_SUBSCRIBER_CONTEXT": "Refresh subscriber context from OSS.",
    "RE_RUN_DIAGNOSTICS": "Re-run the deterministic diagnostic checks for this ticket.",
    "REAPPLY_SESSION_POLICY": "Reapply the subscriber's session policy (network control).",
    "DISCONNECT_REAUTHORIZE": "Disconnect the subscriber's active session and reauthorize (AAA).",
    "REQUEST_COA": "Send a Change-of-Authorization request (AAA/network control).",
    "REQUEST_AAA_RECONCILIATION": "Queue a full AAA session reconciliation.",
    "NAS_REACHABILITY_CHECK": "Run a reachability check against the assigned NAS.",
    "IP_ASSIGNMENT_RECONCILIATION": "Request IP assignment reconciliation from IPAM.",
    "RETRY_PROVISIONING_STEP": "Retry an approved, failed OSS provisioning step.",
    "CREATE_OSS_ORDER": "Create a linked OSS service order for this ticket.",
    "CREATE_WORKFORCE_JOB": "Create a linked Workforce field job for this ticket.",
    "REQUEST_BILLING_REVIEW": "Request a billing review from BSS.",
    "REQUEST_PAYMENT_RECONCILIATION": "Request a payment reconciliation from BSS.",
    "LINK_OUTAGE": "Link this ticket to a known outage.",
    "UNLINK_OUTAGE": "Unlink this ticket from a known outage.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def preview_action(action_type: str, payload: dict | None = None) -> dict:
    action_type = action_type.upper()
    if action_type not in SUPPORT_ACTION_TYPES:
        raise ValidationError(f"unsupported action type {action_type!r}")
    disruptive, requires_auth, _, _ = _ACTION_MAP[action_type]
    return {
        "action_type": action_type,
        "disruptive": disruptive,
        "requires_authorization": requires_auth,
        "description": _ACTION_PREVIEW[action_type],
        "payload": payload or {},
    }


def request_action(
    session: Session,
    tenant_id,
    ticket_id: uuid.UUID,
    *,
    action_type: str,
    payload: dict | None = None,
    actor: str = "system",
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> SupportAction:
    action_type = action_type.upper()
    if action_type not in SUPPORT_ACTION_TYPES:
        raise ValidationError(f"unsupported action type {action_type!r}")
    ticket = ticket_service.get_ticket_or_404(session, tenant_id, ticket_id)
    request_id = correlation(correlation_id)

    if idempotency_key:
        existing = session.scalars(
            select(SupportAction).where(SupportAction.tenant_id == tenant_id,
                                        SupportAction.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            if existing.ticket_id != ticket.id or existing.action_type != action_type:
                raise DuplicateError("idempotency key already used for a different action")
            return existing

    disruptive, requires_auth, _, _ = _ACTION_MAP[action_type]
    status = "AUTHORIZATION_REQUIRED" if (disruptive or requires_auth) else "APPROVED"
    action = SupportAction(
        tenant_id=tenant_id, ticket_id=ticket.id, action_type=action_type,
        status=status, payload=payload or {}, result={},
        idempotency_key=idempotency_key or uuid.uuid4().hex,
        correlation_id=request_id, disruptive=disruptive, requires_authorization=(disruptive or requires_auth),
        requested_by=actor, requested_at=_now(),
    )
    session.add(action)
    session.flush()
    append_event(session, ticket, "ticket.support_action_requested",
                 payload={"action_id": str(action.id), "action_type": action_type, "status": status,
                          "disruptive": disruptive, "payload": payload or {}},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    outbox(session, "support.ticket.support_action_requested.v1", tenant_id, request_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "action_id": str(action.id),
            "action_type": action_type, "status": status})
    return action


def get_action_or_404(session: Session, tenant_id, action_id: uuid.UUID) -> SupportAction:
    action = session.get(SupportAction, action_id)
    if action is None or action.tenant_id != tenant_id:
        raise NotFoundError("support action not found")
    return action


def approve_action(session: Session, tenant_id, action_id: uuid.UUID, *, actor: str, reason: str,
                   correlation_id: str | None = None) -> SupportAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.status not in ("AUTHORIZATION_REQUIRED", "REQUESTED"):
        raise ActionError(f"cannot approve action in state {action.status}")
    if not reason or not reason.strip():
        raise ValidationError("approval requires a reason")
    action.status = "APPROVED"
    action.approved_by = actor
    action.approved_at = _now()
    ticket = session.get(Ticket, action.ticket_id)
    append_event(session, ticket, "ticket.support_action_approved",
                 payload={"action_id": str(action.id), "action_type": action.action_type, "reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    audit(session, tenant_id, "support.action.approved", "support_action", str(action.id), actor=actor, reason=reason,
          correlation_id=correlation_id or action.correlation_id,
          safe_after={"action_type": action.action_type, "status": action.status})
    session.flush()
    return action


def execute_action(session: Session, tenant_id, action_id: uuid.UUID, *, actor: str = "system",
                   correlation_id: str | None = None, now: datetime | None = None) -> SupportAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.status not in ("APPROVED", "QUEUED"):
        raise ActionError(f"cannot execute action in state {action.status}")
    ticket = session.get(Ticket, action.ticket_id)
    action.status = "RUNNING"
    action.executed_at = now or _now()
    action.attempts += 1
    session.flush()
    try:
        result = _dispatch(session, action, ticket, actor=actor, correlation_id=correlation_id or action.correlation_id)
        if result.ok:
            action.status = "SUCCEEDED"
            action.result = {"reference": result.reference, "detail": result.detail}
        else:
            action.status = "MANUAL_INTERVENTION_REQUIRED" if result.error_code == "MANUAL" else "FAILED"
            action.error_code = result.error_code
            action.error_detail = result.error_detail
            action.result = {"reference": result.reference, "detail": result.detail}
    except Exception as error:  # noqa: BLE001 — classified as failure for retry
        action.status = "FAILED"
        action.error_code = "UNEXPECTED"
        action.error_detail = str(error)[:500]
    append_event(session, ticket, "ticket.support_action_completed",
                 payload={"action_id": str(action.id), "action_type": action.action_type, "status": action.status,
                          "attempt": action.attempts, "result": action.result, "error_code": action.error_code},
                 actor_type="system", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    outbox(session, "support.ticket.support_action_completed.v1", tenant_id, correlation_id or action.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "action_id": str(action.id),
            "action_type": action.action_type, "status": action.status})
    session.flush()
    return action


def _dispatch(session: Session, action: SupportAction, ticket: Ticket, *, actor: str, correlation_id: str):
    action_type = action.action_type
    payload = action.payload or {}
    if action_type == "RE_RUN_DIAGNOSTICS":
        snapshot = diagnostic_service.capture_diagnostic_snapshot(session, ticket.tenant_id, ticket,
                                                                  actor=actor, correlation_id=correlation_id)
        return _ActionResultShim(True, str(snapshot.id), {"status": snapshot.status})
    if action_type == "LINK_OUTAGE":
        ticket_service.link_outage(session, ticket.tenant_id, ticket.id,
                                   incident_id=payload.get("incident_id", ""),
                                   incident_number=payload.get("incident_number"), actor=actor, correlation_id=correlation_id)
        return _ActionResultShim(True, payload.get("incident_id"), {"status": "LINKED"})
    if action_type == "UNLINK_OUTAGE":
        ticket_service.unlink_outage(session, ticket.tenant_id, ticket.id, actor=actor, correlation_id=correlation_id)
        return _ActionResultShim(True, None, {"status": "UNLINKED"})
    if action_type == "REFRESH_SUBSCRIBER_CONTEXT":
        adapter = get_adapter("oss")
        result = adapter.get_subscriber_context(payload.get("subscription_id") or ticket.service_subscription_id,
                                                payload.get("subscriber_username") or ticket.subscriber_username)
        return result

    _, _, adapter_name, method = _ACTION_MAP[action_type]
    adapter = get_adapter(adapter_name)

    if action_type in ("DISCONNECT_REAUTHORIZE", "REAPPLY_SESSION_POLICY"):
        result = getattr(adapter, method)(
            subscriber_username=payload.get("subscriber_username") or ticket.subscriber_username,
            ticket_id=str(ticket.id), actor=actor, correlation_id=correlation_id)
    elif action_type == "REQUEST_COA":
        result = adapter.request_coa(subscriber_username=payload.get("subscriber_username") or ticket.subscriber_username,
                                     attributes=payload.get("attributes"), ticket_id=str(ticket.id),
                                     actor=actor, correlation_id=correlation_id)
    elif action_type in ("REQUEST_AAA_RECONCILIATION", "REQUEST_BILLING_REVIEW"):
        result = getattr(adapter, method)(ticket_id=str(ticket.id), actor=actor, correlation_id=correlation_id)
    elif action_type == "NAS_REACHABILITY_CHECK":
        result = adapter.nas_reachability(payload.get("nas_reference") or "unknown",
                                          ticket_id=str(ticket.id), actor=actor, correlation_id=correlation_id)
    elif action_type == "IP_ASSIGNMENT_RECONCILIATION":
        result = adapter.reconcile_assignment(subscription_id=payload.get("subscription_id") or ticket.service_subscription_id,
                                              expected_ip=payload.get("expected_ip"), ticket_id=str(ticket.id),
                                              actor=actor, correlation_id=correlation_id)
    elif action_type == "RETRY_PROVISIONING_STEP":
        result = adapter.retry_order_step(payload.get("order_reference") or ticket.oss_order_id or "",
                                          payload.get("step"), actor=actor, correlation_id=correlation_id)
    elif action_type == "CREATE_OSS_ORDER":
        result = adapter.create_order(
            tenant_id=str(ticket.tenant_id),
            order_type=payload.get("order_type", "SERVICE_REQUEST"),
            customer_id=payload.get("customer_id") or ticket.customer_id,
            subscription_id=payload.get("subscription_id") or ticket.service_subscription_id,
            service_location_id=payload.get("service_location_id") or ticket.service_location_id,
            requested_snapshot=payload.get("requested_snapshot"),
            actor=actor, correlation_id=correlation_id)
    elif action_type == "CREATE_WORKFORCE_JOB":
        result = adapter.create_job(
            tenant_id=str(ticket.tenant_id), job_type=payload.get("job_type", "FIELD_VISIT"),
            ticket_id=str(ticket.id),
            service_location_id=payload.get("service_location_id") or ticket.service_location_id,
            requested_at=payload.get("requested_at"), required_skill=payload.get("required_skill"),
            notes=payload.get("notes"), actor=actor, correlation_id=correlation_id)
    elif action_type == "REQUEST_PAYMENT_RECONCILIATION":
        result = adapter.reconcile_payment(ticket_id=str(ticket.id), actor=actor, correlation_id=correlation_id,
                                           amount=payload.get("amount"))
    else:  # pragma: no cover — guarded by request_action validation
        raise ActionError(f"unsupported action {action_type!r}")

    # Link created external records back onto the ticket.
    if result.ok and action_type == "CREATE_OSS_ORDER":
        ticket_service.link_oss_order(session, ticket.tenant_id, ticket.id, order_id=result.reference,
                                      order_number=result.reference, actor=actor, correlation_id=correlation_id, auto=False)
    if result.ok and action_type == "CREATE_WORKFORCE_JOB":
        ticket_service.link_workforce_job(session, ticket.tenant_id, ticket.id, job_id=result.reference,
                                          job_number=result.reference, actor=actor, correlation_id=correlation_id, auto=False)
    return result


class _ActionResultShim:
    """Small shim so internal actions share the ActionResult contract."""

    def __init__(self, ok: bool, reference, detail: dict):
        self.ok = ok
        self.reference = reference
        self.detail = detail


def retry_action(session: Session, tenant_id, action_id: uuid.UUID, *, actor: str = "system",
                 correlation_id: str | None = None) -> SupportAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.status not in ("FAILED", "TIMED_OUT", "MANUAL_INTERVENTION_REQUIRED"):
        raise ActionError(f"cannot retry action in state {action.status}")
    action.status = "QUEUED"
    action.error_code = None
    action.error_detail = None
    session.flush()
    return action


def cancel_action(session: Session, tenant_id, action_id: uuid.UUID, *, actor: str = "system",
                  reason: str = "", correlation_id: str | None = None) -> SupportAction:
    action = get_action_or_404(session, tenant_id, action_id)
    if action.status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"):
        raise ActionError(f"cannot cancel action in state {action.status}")
    action.status = "CANCELLED"
    ticket = session.get(Ticket, action.ticket_id)
    append_event(session, ticket, "ticket.support_action_cancelled",
                 payload={"action_id": str(action.id), "reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or action.correlation_id)
    session.flush()
    return action
