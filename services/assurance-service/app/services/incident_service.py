"""Incident lifecycle, impact (estimated vs confirmed), root cause, postmortems."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.correlation import ImpactEstimate
from ..domain.exceptions import NotFoundError, RootCauseError
from ..events import outbox
from ..models import (Incident, IncidentAction, IncidentAlertLink, IncidentCommander,
                      IncidentCommunication, IncidentCustomerImpact, IncidentEvent,
                      IncidentResponder, IncidentServiceImpact, IncidentTicketLink,
                      Postmortem, PostmortemActionItem, RootCauseEvidence, RootCauseHypothesis)
from ..state_machine import guarded as incident_guarded

INCIDENT_STATE_FLOW = {
    "DETECTED": {"TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "TRIAGE": {"INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "INVESTIGATING": {"IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "IDENTIFIED": {"MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "MITIGATING": {"MONITORING", "POSTMORTEM_REQUIRED"},
    "MONITORING": {"RESOLVED", "POSTMORTEM_REQUIRED"},
    "RESOLVED": {"CLOSED", "POSTMORTEM_REQUIRED"},
    "POSTMORTEM_REQUIRED": {"CLOSED"},
    "CLOSED": set(),
}

ROOT_CAUSE_FLOW = {
    "OBSERVATION": {"HYPOTHESIS", "REJECTED_HYPOTHESIS"},
    "HYPOTHESIS": {"LIKELY_CAUSE", "CONFIRMED_ROOT_CAUSE", "REJECTED_HYPOTHESIS", "OBSERVATION"},
    "LIKELY_CAUSE": {"CONFIRMED_ROOT_CAUSE", "REJECTED_HYPOTHESIS"},
    "CONFIRMED_ROOT_CAUSE": {"REJECTED_HYPOTHESIS"},
    "REJECTED_HYPOTHESIS": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _transition(incident: Incident, target: str, *, actor: str | None = None):
    if target not in INCIDENT_STATE_FLOW.get(incident.state, set()):
        raise ValueError(f"invalid incident transition {incident.state} -> {target}")
    from_state = incident.state
    incident.state = target
    if target == "RESOLVED":
        incident.resolved_at = _now()
    if target == "CLOSED":
        incident.closed_at = _now()
    return {"from": from_state, "to": target}


def _event(session: Session, incident: Incident, event_type: str, detail: dict, actor: str | None = None):
    row = IncidentEvent(tenant_id=incident.tenant_id, incident_id=incident.id, event_type=event_type,
                        detail=detail, actor=actor, occurred_at=_now())
    session.add(row)
    return row


def create_incident(session: Session, *, tenant_id, title: str, severity: str = "MEDIUM",
                    source: str = "MANUAL", description: str | None = None, alert_id: uuid.UUID | None = None,
                    is_major: bool = False, correlation_id: str | None = None, actor: str | None = None) -> Incident:
    incident = Incident(tenant_id=tenant_id, title=title, state="DETECTED", severity=severity,
                        is_major=is_major, source=source, description=description,
                        detected_at=_now(), correlation_id=correlation_id)
    session.add(incident)
    session.flush()
    _event(session, incident, "CREATED", {"source": source, "actor": actor or "system"}, actor=actor)
    if alert_id:
        session.add(IncidentAlertLink(tenant_id=tenant_id, incident_id=incident.id, alert_id=alert_id))
        session.flush()
        # Promote linked alert to reference this incident.
        from ..models import Alert
        alert = session.scalars(select(Alert).where(Alert.id == alert_id)).first()
        if alert is not None:
            alert.current_incident_id = incident.id
    outbox(session, "assurance.incident_created.v1", tenant_id, correlation_id,
           {"incident_id": str(incident.id), "title": title, "severity": severity,
            "source": source, "is_major": is_major},
           idempotency_key=f"incident-created:{incident.id}")
    return incident


def create_from_alert(session: Session, alert, *, severity: str | None = None, is_major: bool = False,
                      correlation_id: str | None = None, actor: str | None = None) -> Incident:
    title = f"{alert.service} - {alert.alert_name}"
    return create_incident(session, tenant_id=alert.tenant_id, title=title,
                           severity=severity or alert.severity, source="ALERT",
                           description=f"Auto-created from alert {alert.alert_name} on {alert.service}",
                           alert_id=alert.id, is_major=is_major,
                           correlation_id=correlation_id, actor=actor)


def transition(session: Session, incident_id: uuid.UUID, target: str, *, actor: str | None = None,
               detail: dict | None = None) -> Incident:
    incident = _get_incident(session, incident_id)
    change = _transition(incident, target, actor=actor)
    _event(session, incident, "STATE_CHANGE", {**change, **(detail or {})}, actor=actor)
    if target == "RESOLVED":
        outbox(session, "assurance.incident_resolved.v1", incident.tenant_id, None,
               {"incident_id": str(incident.id)}, idempotency_key=f"incident-resolved:{incident.id}")
    else:
        outbox(session, "assurance.incident_updated.v1", incident.tenant_id, None,
               {"incident_id": str(incident.id), "state": target},
               idempotency_key=f"incident-updated:{incident.id}")
    session.flush()
    return incident


def declare_major(session: Session, incident_id: uuid.UUID, *, actor: str | None = None) -> Incident:
    incident = _get_incident(session, incident_id)
    incident.is_major = True
    _event(session, incident, "MAJOR_DECLARED", {"actor": actor}, actor=actor)
    return incident


def _get_incident(session: Session, incident_id: uuid.UUID) -> Incident:
    incident = session.scalars(select(Incident).where(Incident.id == incident_id)).first()
    if incident is None:
        raise NotFoundError("incident not found")
    return incident


def add_commander(session: Session, incident_id: uuid.UUID, user_id: str, role: str = "COMMANDER"):
    incident = _get_incident(session, incident_id)
    session.add(IncidentCommander(tenant_id=incident.tenant_id, incident_id=incident.id,
                                  user_id=user_id, role=role))
    return incident


def add_responder(session: Session, incident_id: uuid.UUID, user_id: str, role: str = "RESPONDER"):
    incident = _get_incident(session, incident_id)
    session.add(IncidentResponder(tenant_id=incident.tenant_id, incident_id=incident.id,
                                  user_id=user_id, role=role))
    return incident


def link_alert(session: Session, incident_id: uuid.UUID, alert_id: uuid.UUID):
    incident = _get_incident(session, incident_id)
    session.add(IncidentAlertLink(tenant_id=incident.tenant_id, incident_id=incident.id, alert_id=alert_id))
    return incident


def link_ticket(session: Session, incident_id: uuid.UUID, ticket_id: str, relationship: str = "RELATED"):
    incident = _get_incident(session, incident_id)
    session.add(IncidentTicketLink(tenant_id=incident.tenant_id, incident_id=incident.id,
                                   ticket_id=ticket_id, relationship=relationship))
    return incident


def add_service_impact(session: Session, incident_id: uuid.UUID, service_id: uuid.UUID,
                       impact_level: str = "PARTIAL"):
    incident = _get_incident(session, incident_id)
    session.add(IncidentServiceImpact(tenant_id=incident.tenant_id, incident_id=incident.id,
                                      service_id=service_id, impact_level=impact_level))
    return incident


def estimate_customer_impact(session: Session, incident_id: uuid.UUID, *, impact_kind: str,
                             estimated_subscribers: int, detail: dict | None = None,
                             impact_ref: str | None = None) -> IncidentCustomerImpact:
    incident = _get_incident(session, incident_id)
    impact_ref = impact_ref or ""
    row = IncidentCustomerImpact(tenant_id=incident.tenant_id, incident_id=incident.id,
                                 impact_kind=impact_kind, impact_ref=impact_ref,
                                 estimated=True, estimated_subscribers=estimated_subscribers,
                                 confirmed_subscribers=0, detail=detail or {})
    session.add(row)
    session.flush()
    outbox(session, "assurance.customer_impact_detected.v1", incident.tenant_id, None,
           {"incident_id": str(incident.id), "impact_kind": impact_kind,
            "estimated_subscribers": estimated_subscribers},
           idempotency_key=f"impact:{incident.id}:{impact_kind}")
    return row


def confirm_customer_impact(session: Session, incident_id: uuid.UUID, *, impact_kind: str,
                            confirmed_subscribers: int, impact_ref: str | None = None) -> IncidentCustomerImpact:
    incident = _get_incident(session, incident_id)
    impact_ref = impact_ref or ""
    existing = session.scalars(select(IncidentCustomerImpact).where(
        IncidentCustomerImpact.incident_id == incident.id,
        IncidentCustomerImpact.impact_kind == impact_kind,
        IncidentCustomerImpact.impact_ref == impact_ref)).first()
    if existing is None:
        existing = IncidentCustomerImpact(tenant_id=incident.tenant_id, incident_id=incident.id,
                                          impact_kind=impact_kind, impact_ref=impact_ref,
                                          estimated=False, estimated_subscribers=0,
                                          confirmed_subscribers=0, detail={})
        session.add(existing)
    existing.estimated = False
    existing.confirmed_subscribers = confirmed_subscribers
    session.flush()
    return existing


def impact_summary(session: Session, incident_id: uuid.UUID) -> ImpactEstimate:
    incident = _get_incident(session, incident_id)
    rows = list(session.scalars(select(IncidentCustomerImpact).where(
        IncidentCustomerImpact.incident_id == incident.id)))
    estimate = ImpactEstimate(
        affected_services=[str(r.service_id) for r in session.scalars(
            select(IncidentServiceImpact.service_id).where(IncidentServiceImpact.incident_id == incident.id))],
        estimated_subscribers=sum(r.estimated_subscribers for r in rows),
        confirmed_subscribers=sum(r.confirmed_subscribers for r in rows),
        business_customers=len(rows),
        revenue_risk_ref=None,
        open_ticket_count=len(list(session.scalars(select(IncidentTicketLink).where(
            IncidentTicketLink.incident_id == incident.id)))),
        estimated=any(r.estimated for r in rows),
    )
    return estimate


def add_communication(session: Session, incident_id: uuid.UUID, *, audience: str, message: str,
                      channel: str = "STATUS_PAGE") -> IncidentCommunication:
    incident = _get_incident(session, incident_id)
    row = IncidentCommunication(tenant_id=incident.tenant_id, incident_id=incident.id,
                                audience=audience, message=message, channel=channel,
                                published_at=_now())
    session.add(row)
    session.flush()
    return row


def create_action(session: Session, incident_id: uuid.UUID, *, action_type: str,
                  description: str | None = None, assigned_to: str | None = None) -> IncidentAction:
    incident = _get_incident(session, incident_id)
    row = IncidentAction(tenant_id=incident.tenant_id, incident_id=incident.id,
                         action_type=action_type, description=description,
                         assigned_to=assigned_to)
    session.add(row)
    session.flush()
    return row


def require_postmortem(session: Session, incident_id: uuid.UUID, *, actor: str | None = None) -> Incident:
    incident = _get_incident(session, incident_id)
    change = _transition(incident, "POSTMORTEM_REQUIRED", actor=actor)
    _event(session, incident, "STATE_CHANGE", change, actor=actor)
    outbox(session, "assurance.postmortem_required.v1", incident.tenant_id, None,
           {"incident_id": str(incident.id)}, idempotency_key=f"postmortem-required:{incident.id}")
    return incident


def create_postmortem(session: Session, incident_id: uuid.UUID, *, tenant_id, summary: str | None = None,
                      root_cause: str | None = None, actor: str | None = None) -> Postmortem:
    incident = _get_incident(session, incident_id)
    if incident.state != "POSTMORTEM_REQUIRED":
        raise ValueError("postmortem requires incident in POSTMORTEM_REQUIRED state")
    existing = session.scalars(select(Postmortem).where(Postmortem.incident_id == incident.id)).first()
    if existing is not None:
        return existing
    pm = Postmortem(tenant_id=tenant_id, incident_id=incident.id, summary=summary,
                    root_cause=root_cause, state="DRAFT")
    session.add(pm)
    session.flush()
    return pm


def add_postmortem_action(session: Session, postmortem_id: uuid.UUID, *, title: str,
                          owner: str | None = None, due_at=None) -> PostmortemActionItem:
    pm = session.scalars(select(Postmortem).where(Postmortem.id == postmortem_id)).first()
    if pm is None:
        raise NotFoundError("postmortem not found")
    row = PostmortemActionItem(tenant_id=pm.tenant_id, postmortem_id=pm.id, title=title,
                               owner=owner, due_at=due_at, state="OPEN")
    session.add(row)
    session.flush()
    return row


# ---------------- Root Cause ----------------

def create_hypothesis(session: Session, incident_id: uuid.UUID, *, hypothesis: str, confidence: float = 0.0,
                      created_by: str | None = None, is_ai_suggestion: bool = False) -> RootCauseHypothesis:
    incident = _get_incident(session, incident_id)
    row = RootCauseHypothesis(tenant_id=incident.tenant_id, incident_id=incident.id,
                              state="OBSERVATION", hypothesis=hypothesis, confidence=confidence,
                              created_by=created_by, is_ai_suggestion=is_ai_suggestion)
    session.add(row)
    session.flush()
    outbox(session, "assurance.root_cause_hypothesis_created.v1", incident.tenant_id, None,
           {"incident_id": str(incident.id), "hypothesis_id": str(row.id)},
           idempotency_key=f"root-cause-hypothesis:{row.id}")
    return row


def add_evidence(session: Session, hypothesis_id: uuid.UUID, *, evidence_type: str, evidence_ref: str,
                 supports: bool = True, detail: dict | None = None) -> RootCauseEvidence:
    h = session.scalars(select(RootCauseHypothesis).where(RootCauseHypothesis.id == hypothesis_id)).first()
    if h is None:
        raise NotFoundError("hypothesis not found")
    row = RootCauseEvidence(tenant_id=h.tenant_id, hypothesis_id=h.id, evidence_type=evidence_type,
                            evidence_ref=evidence_ref, supports=supports, detail=detail or {})
    session.add(row)
    session.flush()
    supporting = [r for r in h.supporting_evidence] if h.supporting_evidence else []
    contradicting = [r for r in h.contradicting_evidence] if h.contradicting_evidence else []
    if supports:
        supporting.append({"evidence_type": evidence_type, "evidence_ref": evidence_ref})
    else:
        contradicting.append({"evidence_type": evidence_type, "evidence_ref": evidence_ref})
    h.supporting_evidence = supporting
    h.contradicting_evidence = contradicting
    return row


def confirm_root_cause(session: Session, hypothesis_id: uuid.UUID, *, confirmed_by: str,
                       evidence_count: int | None = None) -> RootCauseHypothesis:
    """Confirm only with >=1 supporting evidence and no contradicting evidence.
    Temporal coincidence is NEVER auto-confirmed."""
    h = session.scalars(select(RootCauseHypothesis).where(RootCauseHypothesis.id == hypothesis_id)).first()
    if h is None:
        raise NotFoundError("hypothesis not found")
    supporting = len(h.supporting_evidence or [])
    contradicting = len(h.contradicting_evidence or [])
    if contradicting:
        raise RootCauseError("cannot confirm hypothesis with contradicting evidence")
    if supporting < 1:
        raise RootCauseError("cannot confirm hypothesis without supporting evidence")
    if h.state not in ("HYPOTHESIS", "LIKELY_CAUSE", "OBSERVATION"):
        raise RootCauseError(f"cannot confirm hypothesis in state {h.state}")
    h.state = "CONFIRMED_ROOT_CAUSE"
    h.confirmed_by = confirmed_by
    session.flush()
    outbox(session, "assurance.root_cause_confirmed.v1", h.tenant_id, None,
           {"incident_id": str(h.incident_id), "hypothesis_id": str(h.id)},
           idempotency_key=f"root-cause-confirmed:{h.id}")
    return h


def transition_hypothesis(session: Session, hypothesis_id: uuid.UUID, target: str) -> RootCauseHypothesis:
    h = session.scalars(select(RootCauseHypothesis).where(RootCauseHypothesis.id == hypothesis_id)).first()
    if h is None:
        raise NotFoundError("hypothesis not found")
    if target not in ROOT_CAUSE_FLOW.get(h.state, set()):
        raise RootCauseError(f"invalid root-cause transition {h.state} -> {target}")
    h.state = target
    session.flush()
    return h
