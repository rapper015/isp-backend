"""Workforce configuration catalogue: work-order types, versioned templates,
versioned checklist templates + items, service areas, field SLA policies
(versioned) and business calendars.

`tenant_id` is NULL for platform-provided global defaults; runtime resolution
prefers tenant-specific over global."""
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class WorkOrderType(Base, Timestamped):
    __tablename__ = "workforce_work_order_types"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_wotype_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    requires_customer_presence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_supervisor_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_remote_activation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WorkOrderTemplate(Base, Timestamped):
    __tablename__ = "workforce_work_order_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_template_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    work_order_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class WorkOrderTemplateVersion(Base, Timestamped):
    """Immutable published template version. The work order snapshots the exact
    version used during execution."""
    __tablename__ = "workforce_work_order_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_workforce_template_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_work_order_templates.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # definition:
    #  {
    #    "required_skills": [...],
    #    "required_certifications": [...],
    #    "expected_duration_minutes": 90,
    #    "required_equipment": [...],
    #    "required_consumables": [...],
    #    "sla_policy_code": "FIELD_DEFAULT",
    #    "completion_rules": {"require_qa": true, "require_acknowledgement": true,
    #                          "require_proof": ["PHOTOGRAPH","SERIAL_NUMBER"]},
    #    "checklist_template_id": "<uuid>",
    #  }
    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ChecklistTemplate(Base, Timestamped):
    __tablename__ = "workforce_checklist_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_checklist_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    work_order_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ChecklistTemplateVersion(Base, Timestamped):
    __tablename__ = "workforce_checklist_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_workforce_checklist_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_checklist_templates.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChecklistItem(Base, Timestamped):
    __tablename__ = "workforce_checklist_items"
    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_workforce_checklist_item_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_checklist_template_versions.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # rule: {type: REQUIRED|CONDITIONAL|REPEATABLE, depends_on: "<code>", when: {...}}
    rule: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # constraints: {min, max, allowed_values, unit, expected_range, evidence_required}
    constraints: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ServiceArea(Base, Timestamped):
    __tablename__ = "workforce_service_areas"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_service_area_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    pop_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # polygon: [[lat, lng], ...] or bounds {min_lat, min_lng, max_lat, max_lng}
    geometry: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    geofence_radius_m: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FieldSLAPolicy(Base, Timestamped):
    __tablename__ = "workforce_field_sla_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_sla_policy_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class FieldSLAPolicyVersion(Base, Timestamped):
    """Immutable published version of a field SLA policy."""
    __tablename__ = "workforce_field_sla_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_workforce_sla_policy_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_sla_policies.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # definition: {"pause_on_states": [...], "reopen_policy": "RESTART",
    #              "escalation": [{"target": "ARRIVAL", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_DISPATCHER"}]}
    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FieldSLATarget(Base, Timestamped):
    __tablename__ = "workforce_field_sla_targets"
    __table_args__ = (UniqueConstraint("version_id", "priority", "kind", name="uq_workforce_sla_target"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_field_sla_policy_versions.id"), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)  # P1_CRITICAL..P4_LOW or ALL
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # ARRIVAL / TIME_TO_COMPLETE / ...
    business_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class BusinessCalendar(Base, Timestamped):
    __tablename__ = "workforce_calendars"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_workforce_calendar_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    working_hours: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Holiday(Base, Timestamped):
    __tablename__ = "workforce_holidays"
    __table_args__ = (UniqueConstraint("calendar_id", "holiday_date", name="uq_workforce_holiday_calendar_date"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_calendars.id"), nullable=False, index=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
