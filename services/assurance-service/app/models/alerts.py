"""Alert definitions, normalized alerts, routes, silences, notification delivery."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class AlertDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_alert_definitions"
    __table_args__ = (UniqueConstraint("service_id", "name", name="uq_ass_alert_definition"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="MEDIUM", nullable=False)
    impact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    routing_labels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    runbook_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dashboard_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slo_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    auto_resolution_rule: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expected_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AlertDefinitionTest(Base, Timestamped, UuidPk):
    __tablename__ = "ass_alert_definition_tests"

    alert_definition_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(120), nullable=False)
    expected_state: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Alert(Base, Timestamped, UuidPk):
    """Normalized alert with a stable fingerprint and full lifecycle."""

    __tablename__ = "ass_alerts"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_ass_alert_fingerprint"),
        Index("ix_ass_alert_state", "state"),
        Index("ix_ass_alert_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(120), nullable=False)
    alert_name: Mapped[str] = mapped_column(String(120), nullable=False)
    component: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(120), nullable=True)
    severity: Mapped[str] = mapped_column(String(24), default="MEDIUM", nullable=False)
    impact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    labels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    first_observed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    firing_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_incident_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="collector", nullable=False)
    dedup_window_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)


class AlertEvent(Base, Timestamped, UuidPk):
    __tablename__ = "ass_alert_events"
    __table_args__ = (Index("ix_ass_alert_event", "alert_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    transition: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertRoute(Base, Timestamped, UuidPk):
    __tablename__ = "ass_alert_routes"
    __table_args__ = (UniqueConstraint("name", name="uq_ass_alert_route"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    match_labels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    channel: Mapped[str] = mapped_column(String(40), default="NOC_DASHBOARD", nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    escalation_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fallback_route: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AlertSilence(Base, Timestamped, UuidPk):
    __tablename__ = "ass_alert_silences"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    match_labels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)


class NotificationDelivery(Base, Timestamped, UuidPk):
    __tablename__ = "ass_notification_deliveries"
    __table_args__ = (Index("ix_ass_notification_alert", "alert_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    route: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
