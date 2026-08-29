"""Core support ticket aggregate: Ticket, immutable TicketEvent stream,
communications, attachments, SLA instance + pauses, escalations, diagnostic
snapshots, controlled actions, resolution, CSAT and the concurrency-safe
ticket-number sequence.

Every meaningful change appends an immutable TicketEvent. Historical events
must never be edited or deleted. Redis is never authoritative for any of these.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Ticket(Base, Timestamped):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_number", name="uq_support_ticket_tenant_number"),
        Index("ix_support_ticket_status_priority", "status", "priority"),
        Index("ix_support_ticket_customer", "tenant_id", "customer_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    ticket_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_categories.id"), nullable=True, index=True)
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_subcategories.id"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Cross-boundary references (immutable string IDs; never another DB).
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    customer_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    customer_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    subscriber_username: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    service_location_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    billing_account_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    franchise_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    reseller_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_channel: Mapped[str] = mapped_column(String(32), default="CUSTOMER_PORTAL", nullable=False)

    status: Mapped[str] = mapped_column(String(24), default="NEW", nullable=False, index=True)
    customer_status: Mapped[str] = mapped_column(String(24), default="SUBMITTED", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="P3_MEDIUM", nullable=False, index=True)
    impact: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    severity: Mapped[str] = mapped_column(String(8), default="SEV3", nullable=False)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assigned_queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_queues.id"), nullable=True, index=True)
    assigned_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_teams.id"), nullable=True, index=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    assigned_agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_sla_policies.id"), nullable=True)
    sla_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    response_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)

    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by_type: Mapped[str] = mapped_column(String(24), default="CUSTOMER", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    auto_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    csat_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    resolution_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Related records in other bounded contexts (string refs or UUIDs).
    oss_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    oss_order_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nms_incident_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    nms_incident_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workforce_job_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    workforce_job_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    billing_dispute_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    problem_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_tickets.id"), nullable=True, index=True)

    csat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_csat.id"), nullable=True)

    def customer_visible_status(self) -> str:
        from ..state_machine import customer_status

        return customer_status(self.status)


class TicketEvent(Base):
    """Immutable, append-only event stream. Corrected history is a new event."""
    __tablename__ = "support_ticket_events"
    __table_args__ = (
        UniqueConstraint("ticket_id", "aggregate_version", name="uq_support_ticket_event_version"),
        Index("ix_support_ticket_event_ticket", "ticket_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TicketComment(Base, Timestamped):
    __tablename__ = "support_ticket_comments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_message_id", name="uq_support_comment_provider_message"),
        Index("ix_support_ticket_comment_ticket", "ticket_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), default="OUTBOUND", nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="CUSTOMER_PORTAL", nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="PUBLIC_REPLY", nullable=False)
    visibility: Mapped[str] = mapped_column(String(12), default="PUBLIC", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_body: Mapped[str] = mapped_column(Text, nullable=False)
    sender_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recipient_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reply_token: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TicketAttachment(Base, Timestamped):
    __tablename__ = "support_ticket_attachments"
    __table_args__ = (Index("ix_support_attachment_ticket", "ticket_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_comments.id"), nullable=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(12), default="PUBLIC", nullable=False)
    uploader_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    uploader_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    malware_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketWatcher(Base):
    __tablename__ = "support_ticket_watchers"
    __table_args__ = (UniqueConstraint("ticket_id", "watcher_type", "watcher_id", name="uq_support_watcher"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    watcher_type: Mapped[str] = mapped_column(String(24), nullable=False)  # AGENT / TEAM / CUSTOMER / SUPERVIOR
    watcher_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TicketRelationship(Base, Timestamped):
    __tablename__ = "support_ticket_relationships"
    __table_args__ = (UniqueConstraint("from_ticket_id", "to_ticket_id", "relation_type", name="uq_support_relationship"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    from_ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    to_ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class TicketTag(Base):
    __tablename__ = "support_ticket_tags"
    __table_args__ = (UniqueConstraint("ticket_id", "tag", name="uq_support_ticket_tag"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TicketSLA(Base, Timestamped):
    """Authoritative SLA instance for a ticket: immutable policy snapshot +
    exact timer state. Deadlines are absolute; pauses extend them by exactly
    the business time excluded. All state derivable from persisted columns, so
    evaluation is idempotent, restart-safe and reconciliation-capable."""
    __tablename__ = "support_ticket_slas"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_support_ticket_sla"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_sla_policies.id"), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_calendars.id"), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    response_target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    response_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Timer state.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_accumulated_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    at_risk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    breach_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TicketSLAPause(Base):
    """Every pause interval for a TicketSLA. Used for audit + reconciliation."""
    __tablename__ = "support_ticket_sla_pauses"
    __table_args__ = (Index("ix_support_sla_pause_sla", "sla_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    sla_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_ticket_slas.id"), nullable=False)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    elapsed_business_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TicketEscalation(Base, Timestamped):
    __tablename__ = "support_ticket_escalations"
    __table_args__ = (Index("ix_support_escalation_ticket", "ticket_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    raised_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketDiagnosticSnapshot(Base, Timestamped):
    __tablename__ = "support_diagnostic_snapshots"
    __table_args__ = (Index("ix_support_diagnostic_ticket", "ticket_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # context + checks
    captured_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class SupportAction(Base, Timestamped):
    __tablename__ = "support_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_support_action_idempotency"),
        Index("ix_support_action_ticket", "ticket_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(28), default="REQUESTED", nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disruptive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_authorization: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TicketResolution(Base, Timestamped):
    __tablename__ = "support_ticket_resolutions"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_support_ticket_resolution"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    resolution_code: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    customer_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    related_action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_actions.id"), nullable=True)
    related_article_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_knowledge_articles.id"), nullable=True)
    customer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmation_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)  # PENDING / CONFIRMED / REJECTED


class CustomerSatisfaction(Base, Timestamped):
    __tablename__ = "support_csat"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_support_csat_ticket"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), index=True, nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(24), default="CUSTOMER_PORTAL", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_teams.id"), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_categories.id"), nullable=True)
    low_score_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TicketNumberSequence(Base):
    """Concurrency-safe per-tenant, per-year human-readable ticket numbering.

    Increments happen under a conditional UPDATE that returns the new value;
    the (tenant_id, year) primary key makes concurrent increments serializable
    and the ticket_number unique constraint makes any lost update impossible.
    """
    __tablename__ = "support_ticket_number_sequences"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_tenants.id"), primary_key=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    last_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
