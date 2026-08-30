"""SIEM ORM models (`sec_` prefix). All tenant-owned tables are registered in
`tenant_owned` so routing can enforce tenant isolation fail-closed."""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

_PG = False  # uuid.UUID PKs work on both sqlite and postgres via bind defaults


def utcnow():
    return datetime.now(timezone.utc)


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


# Registry used by app/routing.py to enforce tenant scoping fail-closed.
tenant_owned: set[str] = set()


def _register(name: str):
    tenant_owned.add(name)


class SecurityEvent(UUIDMixin, Base):
    __tablename__ = "sec_security_event"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str | None] = mapped_column(String(200))
    target: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    masked_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    digest: Mapped[str] = mapped_column(String(64))          # evidence hash
    prev_hash: Mapped[str | None] = mapped_column(String(64))  # chain link
    block_index: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        Index("ix_sec_event_tenant_time", "tenant_id", "event_time"),
    )


class EvidenceBlock(UUIDMixin, Base):
    __tablename__ = "sec_evidence_block"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(index=True)
    block_index: Mapped[int] = mapped_column(Integer)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    root_hash: Mapped[str] = mapped_column(String(64))
    canonical: Mapped[str] = mapped_column(Text)  # exact serialized chain input
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompliancePolicy(UUIDMixin, Base):
    __tablename__ = "sec_policy"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    rule_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {"field","op","value","severity"}
    severity: Mapped[str] = mapped_column(String(20), default="HIGH")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PolicyViolation(UUIDMixin, Base):
    __tablename__ = "sec_policy_violation"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sec_policy.id"), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sec_security_event.id"))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="HIGH")
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetentionPolicy(UUIDMixin, Base):
    __tablename__ = "sec_retention_policy"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    data_class: Mapped[str] = mapped_column(String(60), index=True)
    retention_days: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20), default="PURGE")  # ARCHIVE | PURGE
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    regulatory_ref: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "data_class", name="uq_sec_retention_class"),)


class ConsentRecord(UUIDMixin, Base):
    __tablename__ = "sec_consent"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    subscriber_id: Mapped[str] = mapped_column(String(120), index=True)
    purpose: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="GRANTED", index=True)
    source: Mapped[str | None] = mapped_column(String(60))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", "purpose",
                                       name="uq_sec_consent_sub_purpose"),)


class DataAccessRequest(UUIDMixin, Base):
    __tablename__ = "sec_data_request"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    requester_id: Mapped[str] = mapped_column(String(120))
    subject_id: Mapped[str] = mapped_column(String(120), index=True)
    request_type: Mapped[str] = mapped_column(String(20))  # ACCESS | ERASURE | PORTABILITY
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_days: Mapped[int] = mapped_column(Integer, default=30)


class SecurityCase(UUIDMixin, Base):
    __tablename__ = "sec_case"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    ref_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM", index=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    assignee: Mapped[str | None] = mapped_column(String(200))
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    breach_impact: Mapped[dict] = mapped_column(JSON, default=dict)
    notification_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CaseEvent(UUIDMixin, Base):
    __tablename__ = "sec_case_event"
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sec_case.id"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    transition: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(UUIDMixin, Base):
    __tablename__ = "sec_audit_log"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource: Mapped[str | None] = mapped_column(String(200))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Vulnerability(UUIDMixin, Base):
    __tablename__ = "sec_vulnerability"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    target: Mapped[str] = mapped_column(String(200))
    scanner: Mapped[str | None] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM", index=True)
    cve: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    remediated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LIRequest(UUIDMixin, Base):
    __tablename__ = "sec_li_request"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    target_subscriber: Mapped[str] = mapped_column(String(120))
    requester: Mapped[str] = mapped_column(String(200))
    authority_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approver_note: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(UUIDMixin, Base):
    __tablename__ = "sec_outbox"
    event_type: Mapped[str] = mapped_column(String(160), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Inbox(UUIDMixin, Base):
    __tablename__ = "sec_inbox"
    message_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(160))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CircleRegion(UUIDMixin, Base):
    """Operator circle/region mapping, India (feature 403)."""
    __tablename__ = "sec_circle_region"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    operator: Mapped[str] = mapped_column(String(120))
    circle_name: Mapped[str] = mapped_column(String(160), index=True)
    state_codes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "circle_name", name="uq_sec_circle"),)


class GeoBlockRule(UUIDMixin, Base):
    """Restrict services per region (feature 1164)."""
    __tablename__ = "sec_geo_block_rule"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    service: Mapped[str] = mapped_column(String(120))
    region_code: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(20), default="BLOCK")  # BLOCK | ALLOW
    status: Mapped[str] = mapped_column(String(20), default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "service", "region_code", name="uq_sec_geo_rule"),)


class ThreatPlaybook(UUIDMixin, Base):
    """Guided threat-hunting playbooks (feature 1236)."""
    __tablename__ = "sec_threat_playbook"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    tactic: Mapped[str | None] = mapped_column(String(120))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    executions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdaptiveMfaRule(UUIDMixin, Base):
    """Context-based MFA triggers (feature 1370)."""
    __tablename__ = "sec_adaptive_mfa_rule"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)  # {"risk_score": 70, "geo_mismatch": true, ...}
    trigger_action: Mapped[str] = mapped_column(String(20), default="CHALLENGE")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LegalNotice(UUIDMixin, Base):
    """Automated legal notice workflows (feature 1280)."""
    __tablename__ = "sec_legal_notice"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    notice_type: Mapped[str] = mapped_column(String(120), index=True)  # e.g. breach, lawful-interception, dmca
    subject: Mapped[str] = mapped_column(String(200))
    recipient: Mapped[str] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT -> PROCESSING -> SERVED -> CLOSED
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForensicInvestigation(UUIDMixin, Base):
    """Digital forensics engine: evidence chain + timeline + findings (feature 1443)."""
    __tablename__ = "sec_forensic_investigation"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    case_ref: Mapped[str] = mapped_column(String(200), index=True)
    scope: Mapped[str] = mapped_column(String(200))
    evidence_items: Mapped[list] = mapped_column(JSON, default=list)  # hashes, sources
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN -> COMPLETE
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


for _t in (SecurityEvent, EvidenceBlock, CompliancePolicy, PolicyViolation,
           RetentionPolicy, ConsentRecord, DataAccessRequest, SecurityCase,
           CaseEvent, AuditLog, Vulnerability, LIRequest,
           CircleRegion, GeoBlockRule, ThreatPlaybook, AdaptiveMfaRule,
           LegalNotice, ForensicInvestigation):
    _register(_t.__tablename__)
