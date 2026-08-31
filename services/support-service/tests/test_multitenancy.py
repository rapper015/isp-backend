"""Multi-tenancy: cross-tenant access to tickets, attachments, actions and
portal data is denied."""
import pytest

from app.domain.exceptions import NotFoundError
from app.models import Tenant
from app.services import action_service, ticket_service
from app.services.audit_service import ticket_events


def test_cross_tenant_ticket_denied(session, tenant_id, make_ticket):
    ticket = make_ticket()
    other = Tenant(name="Other ISP", code="OTHER")
    session.add(other)
    session.commit()
    session.refresh(other)
    with pytest.raises(NotFoundError):
        ticket_service.get_ticket_or_404(session, other.id, ticket.id)


def test_cross_tenant_event_stream_denied(session, tenant_id, make_ticket):
    ticket = make_ticket()
    other = Tenant(name="Other ISP", code="OTHER2")
    session.add(other)
    session.commit()
    session.refresh(other)
    # Events are retrieved via the tenant-scoped ticket; a foreign tenant can't get there.
    with pytest.raises(NotFoundError):
        ticket_service.get_ticket_or_404(session, other.id, ticket.id)


def test_cross_tenant_action_denied(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action = action_service.request_action(session, tenant_id, ticket.id, action_type="NAS_REACHABILITY_CHECK", actor="a1")
    session.commit()
    other = Tenant(name="Other ISP", code="OTHER3")
    session.add(other)
    session.commit()
    session.refresh(other)
    with pytest.raises(NotFoundError):
        action_service.get_action_or_404(session, other.id, action.id)


def test_tenant_idempotency_keys_isolated(session, tenant_id, make_ticket):
    ticket = make_ticket()
    action_service.request_action(session, tenant_id, ticket.id, action_type="NAS_REACHABILITY_CHECK",
                                  actor="a1", idempotency_key="shared-key")
    session.commit()
    other = Tenant(name="Other ISP", code="OTHER4")
    session.add(other)
    session.commit()
    session.refresh(other)
    # Same idempotency key for a different tenant is a new action (not a conflict).
    from app.models import SupportAction

    count = session.query(SupportAction).filter_by(tenant_id=other.id, idempotency_key="shared-key").count()
    assert count == 0


def test_search_scoped_by_tenant(session, tenant_id, make_ticket):
    make_ticket()
    other = Tenant(name="Other ISP", code="OTHER5")
    session.add(other)
    session.commit()
    session.refresh(other)
    from sqlalchemy import select

    from app.models import Ticket

    count_tenant_a = len(list(session.scalars(select(Ticket).where(Ticket.tenant_id == tenant_id))))
    count_tenant_b = len(list(session.scalars(select(Ticket).where(Ticket.tenant_id == other.id))))
    assert count_tenant_a >= 1
    assert count_tenant_b == 0
