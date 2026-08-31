"""Incidents, impact, root-cause evidence, postmortems, change events."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class Incident(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incidents"
    __table_args__ = (Index("ix_ass_incident_state", "state"), Index("ix_ass_incident_tenant", "tenant_id"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DETECTED", nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="MEDIUM", nullable=False)
    is_major: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="MANUAL", nullable=False)  # ALERT|NMS|SECURITY|...
    commander: Mapped[str | None] = mapped_column(String(128), nullable=True)
    technical_lead: Mapped[str | None] = mapped_column(String(128), nullable=True)
    communication_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidentiality: Mapped[str] = mapped_column(String(24), default="INTERNAL", nullable=False)


class IncidentEvent(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_events"
    __table_args__ = (Index("ix_ass_incident_event", "incident_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)  # STATE_CHANGE|TIMELINE|COMMS|ACTION
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IncidentAlertLink(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_alert_links"
    __table_args__ = (UniqueConstraint("incident_id", "alert_id", name="uq_ass_incident_alert"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)


class IncidentServiceImpact(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_service_impacts"
    __table_args__ = (UniqueConstraint("incident_id", "service_id", name="uq_ass_incident_service_impact"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    impact_level: Mapped[str] = mapped_column(String(16), default="PARTIAL", nullable=False)


class IncidentCustomerImpact(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_customer_impacts"
    __table_args__ = (UniqueConstraint("incident_id", "impact_kind", "impact_ref", name="uq_ass_incident_customer_impact"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    impact_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    impact_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    estimated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    estimated_subscribers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmed_subscribers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class IncidentCommander(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_commanders"
    __table_args__ = (UniqueConstraint("incident_id", "user_id", name="uq_ass_incident_commander"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="COMMANDER", nullable=False)


class IncidentResponder(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_responders"
    __table_args__ = (UniqueConstraint("incident_id", "user_id", name="uq_ass_incident_responder"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(60), default="RESPONDER", nullable=False)


class IncidentCommunication(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_communications"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    audience: Mapped[str] = mapped_column(String(24), default="INTERNAL", nullable=False)  # INTERNAL|CUSTOMER_SAFE
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(40), default="STATUS_PAGE", nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IncidentAction(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_actions"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IncidentTicketLink(Base, Timestamped, UuidPk):
    __tablename__ = "ass_incident_ticket_links"
    __table_args__ = (UniqueConstraint("incident_id", "ticket_id", name="uq_ass_incident_ticket"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    ticket_id: Mapped[str] = mapped_column(String(120), nullable=False)
    relationship: Mapped[str] = mapped_column(String(24), default="RELATED", nullable=False)


class Postmortem(Base, Timestamped, UuidPk):
    __tablename__ = "ass_postmortems"
    __table_args__ = (UniqueConstraint("incident_id", name="uq_ass_postmortem_incident"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_impact: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    timeline: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributing_factors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    what_worked: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    what_failed: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    recurrence_incident_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class PostmortemActionItem(Base, Timestamped, UuidPk):
    __tablename__ = "ass_postmortem_action_items"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    postmortem_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)  # OPEN|IN_PROGRESS|DONE
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RootCauseHypothesis(Base, Timestamped, UuidPk):
    __tablename__ = "ass_root_cause_hypotheses"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), default="OBSERVATION", nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    supporting_evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    contradicting_evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_ai_suggestion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RootCauseEvidence(Base, Timestamped, UuidPk):
    __tablename__ = "ass_root_cause_evidence"
    __table_args__ = (UniqueConstraint("hypothesis_id", "evidence_type", "evidence_ref",
                                       name="uq_ass_root_cause_evidence"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    supports: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ChangeEvent(Base, Timestamped, UuidPk):
    __tablename__ = "ass_change_events"
    __table_args__ = (Index("ix_ass_change_tenant", "tenant_id"), Index("ix_ass_change_occurred", "occurred_at"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    change_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
