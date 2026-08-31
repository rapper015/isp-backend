"""Incident lifecycle, impact estimate vs confirmed, ticket/alert linking,
postmortems and action items."""
import uuid
from datetime import datetime, timezone

import pytest

from app.models import (Incident, IncidentCustomerImpact, IncidentTicketLink,
                        Postmortem, PostmortemActionItem)
from app.services import alert_service, incident_service


def _now():
    return datetime.now(timezone.utc)


def _incident(session, tenant_id, **kw):
    kwargs = {"title": "Test incident", "tenant_id": tenant_id}
    kwargs.update(kw)
    return incident_service.create_incident(session, **kwargs)


def test_create_incident_default_state(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    assert inc.state == "DETECTED"
    assert inc.source == "MANUAL"
    assert inc.is_major is False


def test_create_from_alert(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="radius_down", tenant_id=tenant_id, severity="CRITICAL")
    session.commit()
    inc = incident_service.create_from_alert(session, alert)
    session.commit()
    assert inc.source == "ALERT"
    assert inc.severity == "CRITICAL"
    assert alert.current_incident_id == inc.id


def test_transition_flow(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    for target in ("TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "RESOLVED", "CLOSED"):
        inc = incident_service.transition(session, inc.id, target, actor="noc-1")
        session.commit()
    assert inc.state == "CLOSED"
    assert inc.resolved_at is not None
    assert inc.closed_at is not None


def test_invalid_transition_rejected(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    with pytest.raises(ValueError):
        incident_service.transition(session, inc.id, "RESOLVED", actor="x")  # DETECTED->RESOLVED not allowed


def test_declare_major(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    incident_service.declare_major(session, inc.id, actor="noc-1")
    session.commit()
    assert inc.is_major is True


def test_link_alert_and_ticket(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="billing", alert_name="payments_down", tenant_id=tenant_id, severity="HIGH")
    session.commit()
    inc = _incident(session, tenant_id)
    session.commit()
    incident_service.link_alert(session, inc.id, alert.id)
    incident_service.link_ticket(session, inc.id, "SUP-1001", relationship="RELATED")
    session.commit()
    ticket = session.query(IncidentTicketLink).filter(IncidentTicketLink.incident_id == inc.id).first()
    assert ticket.ticket_id == "SUP-1001"


def test_impact_estimate_then_confirm(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    est = incident_service.estimate_customer_impact(
        session, inc.id, impact_kind="INTERNET", estimated_subscribers=120, detail={"pop": "POP-1"})
    session.commit()
    assert est.estimated is True
    summary = incident_service.impact_summary(session, inc.id)
    assert summary.estimated_subscribers == 120
    assert summary.estimated is True
    conf = incident_service.confirm_customer_impact(
        session, inc.id, impact_kind="INTERNET", confirmed_subscribers=87)
    session.commit()
    assert conf.estimated is False
    summary2 = incident_service.impact_summary(session, inc.id)
    assert summary2.confirmed_subscribers == 87
    assert summary2.estimated is False


def test_require_postmortem_and_create(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    for target in ("TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "RESOLVED"):
        incident_service.transition(session, inc.id, target, actor="noc-1")
    session.commit()
    incident_service.require_postmortem(session, inc.id, actor="noc-1")
    session.commit()
    assert inc.state == "POSTMORTEM_REQUIRED"
    pm = incident_service.create_postmortem(session, inc.id, tenant_id=tenant_id,
                                            summary="Full outage", root_cause="Core router crashed")
    session.commit()
    assert pm.state == "DRAFT"
    item = incident_service.add_postmortem_action(session, pm.id, title="Upgrade router firmware",
                                                  owner="net-eng")
    session.commit()
    assert item.state == "OPEN"


def test_postmortem_requires_resolved_state(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    with pytest.raises(ValueError):
        incident_service.create_postmortem(session, inc.id, tenant_id=tenant_id)


def test_communications_and_actions(defaults, session, tenant_id):
    inc = _incident(session, tenant_id)
    session.commit()
    incident_service.add_communication(session, inc.id, audience="INTERNAL", message="Investigating")
    incident_service.create_action(session, inc.id, action_type="MITIGATION", description="Restart radius")
    session.commit()
    assert len(incident_service.impact_summary(session, inc.id).to_dict()) > 0
