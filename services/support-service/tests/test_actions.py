"""Controlled support action tests: preview, approval flow, idempotency,
disruptive-action authorization, retry, cancel and internal link actions."""
import pytest

from app.domain.exceptions import ActionError, DuplicateError
from app.integrations.fakes import STATE
from app.services import action_service, ticket_service


def _request(session, tenant_id, ticket, action_type, **kw):
    return action_service.request_action(session, tenant_id, ticket.id, action_type=action_type,
                                         actor="agent-1", **kw)


def test_preview_disruptive(session, tenant_id, make_ticket):
    preview = action_service.preview_action("DISCONNECT_REAUTHORIZE")
    assert preview["disruptive"] is True
    assert preview["requires_authorization"] is True
    assert preview["description"]


def test_disruptive_action_requires_approval(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "DISCONNECT_REAUTHORIZE",
                      payload={"subscriber_username": "subs-0001"})
    session.commit()
    assert action.status == "AUTHORIZATION_REQUIRED"
    # Cannot execute before approval.
    with pytest.raises(ActionError):
        action_service.execute_action(session, tenant_id, action.id)
    action = action_service.approve_action(session, tenant_id, action.id, actor="sup-1", reason="confirmed with customer")
    session.commit()
    assert action.status == "APPROVED"
    action = action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"
    assert action.result.get("reference")


def test_non_disruptive_action_auto_approved(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "REFRESH_SUBSCRIBER_CONTEXT")
    session.commit()
    assert action.status == "APPROVED"


def test_idempotent_request_returns_same_action(session, tenant_id, make_ticket):
    ticket = make_ticket()
    a = _request(session, tenant_id, ticket, "NAS_REACHABILITY_CHECK", idempotency_key="k-1")
    session.commit()
    b = _request(session, tenant_id, ticket, "NAS_REACHABILITY_CHECK", idempotency_key="k-1")
    session.commit()
    assert a.id == b.id


def test_idempotency_key_cannot_be_reused_differently(session, tenant_id, make_ticket):
    ticket = make_ticket()
    _request(session, tenant_id, ticket, "NAS_REACHABILITY_CHECK", idempotency_key="k-2")
    session.commit()
    with pytest.raises(DuplicateError):
        _request(session, tenant_id, ticket, "DISCONNECT_REAUTHORIZE", idempotency_key="k-2")


def test_failed_action_can_retry(session, tenant_id, make_ticket):
    ticket = make_ticket()
    STATE.fail["aaa"] = "unreachable"
    action = _request(session, tenant_id, ticket, "DISCONNECT_REAUTHORIZE")
    session.commit()
    action_service.approve_action(session, tenant_id, action.id, actor="sup", reason="ok")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "FAILED"
    assert action.error_code == "unreachable"
    STATE.fail["aaa"] = None
    action = action_service.retry_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "QUEUED"
    action = action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"


def test_cancel_action(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "DISCONNECT_REAUTHORIZE")
    session.commit()
    action = action_service.cancel_action(session, tenant_id, action.id, actor="agent-1", reason="customer withdrew")
    session.commit()
    assert action.status == "CANCELLED"


def test_link_outage_action(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "LINK_OUTAGE", payload={"incident_id": "INC-1", "incident_number": "INC-1"})
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"
    session.refresh(ticket)
    assert ticket.nms_incident_id == "INC-1"


def test_create_oss_order_links_ticket(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "CREATE_OSS_ORDER", payload={"order_type": "SERVICE_RELOCATION"})
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"
    session.refresh(ticket)
    assert ticket.oss_order_id == action.result["reference"]
    assert ticket.status == "PENDING_OSS_ORDER"


def test_create_workforce_job_links_ticket(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "CREATE_WORKFORCE_JOB", payload={"job_type": "FIELD_VISIT"})
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"
    session.refresh(ticket)
    assert ticket.workforce_job_id == action.result["reference"]
    assert ticket.status == "PENDING_FIELD_VISIT"


def test_action_events_and_outbox(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = _request(session, tenant_id, ticket, "REQUEST_BILLING_REVIEW")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    from app.services.audit_service import ticket_events
    from app.models import OutboxEvent
    from sqlalchemy import select

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.support_action_requested" in events
    assert "ticket.support_action_completed" in events
    outbox_types = list(session.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == tenant_id)))
    assert "support.ticket.support_action_requested.v1" in outbox_types
    assert "support.ticket.support_action_completed.v1" in outbox_types
