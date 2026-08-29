"""Core work-order aggregate and related entities.

Appointments, field visits, check-in/out, assignments, dispatch, checklists,
proof, QA, field SLA and inventory usage are distinct concepts stored
separately. Every meaningful change appends an immutable WorkOrderEvent."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class WorkOrder(Base, Timestamped):
    __tablename__ = "workforce_work_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "work_order_number", name="uq_workforce_wo_tenant_number"),
        Index("ix_workforce_wo_state", "status", "priority"),
        Index("ix_workforce_wo_customer", "tenant_id", "customer_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    work_order_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    template_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="P3_MEDIUM", nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(8), default="SEV3", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False, index=True)
    dispatch_state: Mapped[str] = mapped_column(String(16), default="UNASSIGNED", nullable=False)
    source_channel: Mapped[str] = mapped_column(String(16), default="API", nullable=False)

    # Cross-boundary references (immutable string IDs).
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    service_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    service_location_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    oss_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    oss_order_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    support_ticket_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    support_ticket_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nms_incident_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    billing_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    franchise_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    reseller_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service_area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_service_areas.id"), nullable=True, index=True)

    # Location (service location snapshot for dispatch/geofence).
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Assignment / appointment
    assigned_technician_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=True, index=True)
    assigned_technician_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_appointment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    safety_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_requirements: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    template_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Field SLA (snapshot + live state mirrors the authoritative FieldSLAInstance).
    field_sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_field_sla_policies.id"), nullable=True)
    field_sla_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    field_sla_status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    arrival_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    checklist_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class WorkOrderEvent(Base):
    """Immutable, append-only event stream. Corrected history is a new event."""
    __tablename__ = "workforce_work_order_events"
    __table_args__ = (
        UniqueConstraint("work_order_id", "aggregate_version", name="uq_workforce_wo_event_version"),
        Index("ix_workforce_wo_event_order", "work_order_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
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


class WorkOrderRelationship(Base, Timestamped):
    __tablename__ = "workforce_work_order_relationships"
    __table_args__ = (UniqueConstraint("from_work_order_id", "to_work_order_id", "relation_type", name="uq_workforce_wo_relationship"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    from_work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    to_work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WorkOrderAssignment(Base, Timestamped):
    __tablename__ = "workforce_work_order_assignments"
    __table_args__ = (Index("ix_workforce_assignment_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    technician_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    team_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)  # ACTIVE / REJECTED / REASSIGNED
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Appointment(Base, Timestamped):
    __tablename__ = "workforce_appointments"
    __table_args__ = (Index("ix_workforce_appointment_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(28), default="PROPOSED", nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    customer_preferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FieldVisit(Base, Timestamped):
    __tablename__ = "workforce_field_visits"
    __table_args__ = (Index("ix_workforce_visit_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_appointments.id"), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PLANNED", nullable=False)
    technician_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VisitCheckIn(Base, Timestamped):
    __tablename__ = "workforce_visit_checkins"
    __table_args__ = (UniqueConstraint("visit_id", name="uq_workforce_visit_checkin"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_visits.id"), nullable=False)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    device_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_from_expected_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="ONLINE", nullable=False)  # ONLINE / OFFLINE
    network_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exception_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    override_approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VisitCheckOut(Base, Timestamped):
    __tablename__ = "workforce_visit_checkouts"
    __table_args__ = (UniqueConstraint("visit_id", name="uq_workforce_visit_checkout"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_visits.id"), nullable=False)
    server_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    device_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="ONLINE", nullable=False)
    exception_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    override_approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DispatchPlan(Base, Timestamped):
    __tablename__ = "workforce_dispatch_plans"
    __table_args__ = (UniqueConstraint("tenant_id", "plan_date", "technician_id", name="uq_workforce_dispatch_plan"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    plan_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    # ordered list of work_order_ids with travel buffers
    sequence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # optimistic concurrency
    edited_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimeEntry(Base, Timestamped):
    __tablename__ = "workforce_time_entries"
    __table_args__ = (Index("ix_workforce_time_entry_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="API", nullable=False)


class WorkOrderBlocker(Base, Timestamped):
    __tablename__ = "workforce_work_order_blockers"
    __table_args__ = (Index("ix_workforce_blocker_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    blocker_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    raised_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MaterialRequirement(Base, Timestamped):
    __tablename__ = "workforce_material_requirements"
    __table_args__ = (UniqueConstraint("work_order_id", "material_code", name="uq_workforce_mat_req"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    material_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="UNIT", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="REQUIRED", nullable=False)  # REQUIRED / RESERVED / ISSUED / SATISFIED


class MaterialUsage(Base, Timestamped):
    __tablename__ = "workforce_material_usage"
    __table_args__ = (Index("ix_workforce_mat_usage_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    material_code: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_type: Mapped[str] = mapped_column(String(16), default="CONSUMED", nullable=False)
    inventory_transaction_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    technician_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DeviceInstallation(Base, Timestamped):
    __tablename__ = "workforce_device_installations"
    __table_args__ = (
        UniqueConstraint("serial_number", name="uq_workforce_device_serial"),
        UniqueConstraint("mac_address", name="uq_workforce_device_mac"),
        Index("ix_workforce_device_wo", "work_order_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    device_type: Mapped[str] = mapped_column(String(24), nullable=False)  # ONT / ROUTER / ...
    serial_number: Mapped[str] = mapped_column(String(64), nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="INSTALLED", nullable=False)
    service_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inventory_transaction_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    installed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkOrderChecklist(Base, Timestamped):
    __tablename__ = "workforce_work_order_checklists"
    __table_args__ = (UniqueConstraint("work_order_id", name="uq_workforce_wo_checklist"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    checklist_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checklist_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # exact version used
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChecklistResponse(Base, Timestamped):
    __tablename__ = "workforce_checklist_responses"
    __table_args__ = (UniqueConstraint("checklist_id", "item_code", name="uq_workforce_checklist_response_item"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    checklist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order_checklists.id"), nullable=False, index=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProofOfWork(Base, Timestamped):
    __tablename__ = "workforce_proof_of_work"
    __table_args__ = (
        UniqueConstraint("tenant_id", "evidence_key", name="uq_workforce_proof_evidence_key"),
        Index("ix_workforce_proof_wo", "work_order_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workforce_field_visits.id"), nullable=True)
    checklist_item_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capture_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upload_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    technician_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    verification_state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audit_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FieldAttachment(Base, Timestamped):
    __tablename__ = "workforce_field_attachments"
    __table_args__ = (Index("ix_workforce_attachment_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploader_type: Mapped[str] = mapped_column(String(16), default="TECHNICIAN", nullable=False)
    uploader_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    malware_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerAcknowledgement(Base, Timestamped):
    __tablename__ = "workforce_customer_acknowledgements"
    __table_args__ = (UniqueConstraint("work_order_id", name="uq_workforce_customer_ack"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    masked_recipient: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consent_text_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="CONFIRMED", nullable=False)
    exception: Mapped[str | None] = mapped_column(String(300), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)


class QualityReview(Base, Timestamped):
    __tablename__ = "workforce_quality_reviews"
    __table_args__ = (Index("ix_workforce_qa_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)  # APPROVED / REJECTED / REWORK_REQUIRED
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    review_checks: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FieldSLAInstance(Base, Timestamped):
    __tablename__ = "workforce_field_sla_instances"
    __table_args__ = (UniqueConstraint("work_order_id", name="uq_workforce_field_sla"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_sla_policies.id"), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_calendars.id"), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    arrival_target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    arrival_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completion_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_accumulated_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    at_risk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    breach_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FieldSLAPause(Base):
    __tablename__ = "workforce_field_sla_pauses"
    __table_args__ = (Index("ix_workforce_field_sla_pause", "sla_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    sla_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_sla_instances.id"), nullable=False)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    policy_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    elapsed_business_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class FieldEscalation(Base, Timestamped):
    __tablename__ = "workforce_field_escalations"
    __table_args__ = (Index("ix_workforce_field_escalation_wo", "work_order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    raised_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfflineCommand(Base, Timestamped):
    __tablename__ = "workforce_offline_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_command_id", name="uq_workforce_offline_command"),
        Index("ix_workforce_offline_wo", "work_order_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    client_command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    command: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    local_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="RECEIVED", nullable=False, index=True)
    entity_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WorkOrderNumberSequence(Base):
    """Concurrency-safe per-tenant, per-year human-readable work-order numbering."""
    __tablename__ = "workforce_work_order_number_sequences"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), primary_key=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    last_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WorkOrderResult(Base, Timestamped):
    """Immutable result record for a completed/failed work order."""
    __tablename__ = "workforce_work_order_results"
    __table_args__ = (UniqueConstraint("work_order_id", name="uq_workforce_work_order_result"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_orders.id"), nullable=False)
    result_code: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
