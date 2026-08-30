"""Workforce ORM models (`workforce_` prefix). All tenant-owned tables are
registered in `tenant_owned` for fail-closed tenant scoping."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint, Index, func)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


tenant_owned: set[str] = set()


def _register(name: str):
    tenant_owned.add(name)


class Technician(UUIDMixin, Base):
    __tablename__ = "workforce_technician"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE", index=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    territories: Mapped[list] = mapped_column(JSON, default=list)
    rating: Mapped[float] = mapped_column(Float, default=0.0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lon: Mapped[float | None] = mapped_column(Float)
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkOrder(UUIDMixin, Base):
    __tablename__ = "workforce_work_order"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    ref_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    type: Mapped[str] = mapped_column(String(30), default="INSTALLATION", index=True)
    customer_id: Mapped[str | None] = mapped_column(String(120), index=True)
    address: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="CREATED", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    technician_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_technician.id"))
    source_ticket_id: Mapped[str | None] = mapped_column(String(80))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_wo_tenant_status", "tenant_id", "status"),)


class Assignment(UUIDMixin, Base):
    __tablename__ = "workforce_assignment"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technician.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ASSIGNED")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class Appointment(UUIDMixin, Base):
    __tablename__ = "workforce_appointment"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"))
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="CONFIRMATION_PENDING")


class Visit(UUIDMixin, Base):
    __tablename__ = "workforce_visit"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technician.id"))
    visit_type: Mapped[str] = mapped_column(String(20), default="SITE")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProofOfWork(UUIDMixin, Base):
    __tablename__ = "workforce_proof"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_visit.id"))
    kind: Mapped[str] = mapped_column(String(20), default="PHOTO")
    evidence_key: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "evidence_key", name="uq_wo_proof_evidence"),)


class InventoryItem(UUIDMixin, Base):
    __tablename__ = "workforce_inventory_item"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    item_type: Mapped[str] = mapped_column(String(80), index=True)
    serial_number: Mapped[str | None] = mapped_column(String(120))
    mac_address: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="IN_STOCK", index=True)
    assigned_technician_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_technician.id"))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_work_order.id"))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consumable(UUIDMixin, Base):
    __tablename__ = "workforce_consumable"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(120))
    sku: Mapped[str] = mapped_column(String(120), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    low_threshold: Mapped[int] = mapped_column(Integer, default=5)
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_wo_consumable_sku"),)


class Consumption(UUIDMixin, Base):
    __tablename__ = "workforce_consumption"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"))
    consumable_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_consumable.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Shift(UUIDMixin, Base):
    __tablename__ = "workforce_shift"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technician.id"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")


class Feedback(UUIDMixin, Base):
    __tablename__ = "workforce_feedback"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(120))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Escalation(UUIDMixin, Base):
    __tablename__ = "workforce_escalation"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    level: Mapped[str] = mapped_column(String(20), default="LEVEL_1")
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    escalated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FieldSLA(UUIDMixin, Base):
    __tablename__ = "workforce_field_sla"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    sla_minutes: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    breached: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_minutes: Mapped[int | None] = mapped_column(Integer)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TechnicianKPI(UUIDMixin, Base):
    __tablename__ = "workforce_kpi"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technician.id"), index=True)
    period: Mapped[str] = mapped_column(String(20), default="DAY")
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    avg_rating: Mapped[float] = mapped_column(Float, default=0.0)
    sla_compliance_pct: Mapped[float] = mapped_column(Float, default=0.0)
    productivity_score: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "technician_id", "period", name="uq_wo_kpi"),)


class ChecklistTemplate(UUIDMixin, Base):
    __tablename__ = "workforce_checklist_template"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_type: Mapped[str] = mapped_column(String(30), index=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("tenant_id", "work_order_type", name="uq_wo_checklist_type"),)


class SiteCheck(UUIDMixin, Base):
    __tablename__ = "workforce_site_check"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="SITE_FEASIBILITY")
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_by: Mapped[str | None] = mapped_column(String(200))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Handover(UUIDMixin, Base):
    __tablename__ = "workforce_handover"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order.id"), index=True)
    accepted_by: Mapped[str | None] = mapped_column(String(200))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)


class WorkforceAuditLog(UUIDMixin, Base):
    __tablename__ = "workforce_audit_log"
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource: Mapped[str | None] = mapped_column(String(200))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Outbox(UUIDMixin, Base):
    __tablename__ = "workforce_outbox"
    event_type: Mapped[str] = mapped_column(String(160), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Inbox(UUIDMixin, Base):
    __tablename__ = "workforce_inbox"
    message_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(160))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


for _t in (Technician, WorkOrder, Assignment, Appointment, Visit, ProofOfWork,
           InventoryItem, Consumable, Consumption, Shift, Feedback, Escalation,
           FieldSLA, TechnicianKPI, ChecklistTemplate, SiteCheck, Handover,
           WorkforceAuditLog):
    _register(_t.__tablename__)
