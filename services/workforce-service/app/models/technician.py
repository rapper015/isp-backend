"""Technician profiles, skills, certifications, availability, shifts and
operational status.

Technician operational status is a separate concern from work-order state.
Profiles reference the platform identity/account system (user_ref); the
workforce service never duplicates authentication users."""
import uuid
from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class TechnicianProfile(Base, Timestamped):
    __tablename__ = "workforce_technicians"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_ref", name="uq_workforce_technician_user"),
        Index("ix_workforce_technician_status", "tenant_id", "operational_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    user_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # platform identity reference
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    employment_type: Mapped[str] = mapped_column(String(16), default="EMPLOYEE", nullable=False)
    franchise_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    branch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supervisor_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Operational status (separate from work-order state).
    operational_status: Mapped[str] = mapped_column(String(24), default="OFF_SHIFT", nullable=False, index=True)
    # Location
    base_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    vehicle_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inventory_location_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_daily_capacity: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    shift_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    shift_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    emergency_contact_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supported_work_order_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    service_area_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TechnicianSkill(Base, Timestamped):
    __tablename__ = "workforce_technician_skills"
    __table_args__ = (UniqueConstraint("technician_id", "skill", name="uq_workforce_technician_skill"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    skill: Mapped[str] = mapped_column(String(64), nullable=False)
    proficiency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1..5


class TechnicianCertification(Base, Timestamped):
    __tablename__ = "workforce_technician_certifications"
    __table_args__ = (UniqueConstraint("technician_id", "certification", name="uq_workforce_technician_cert"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    certification: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TechnicianAvailability(Base, Timestamped):
    __tablename__ = "workforce_technician_availability"
    __table_args__ = (UniqueConstraint("technician_id", "available_date", name="uq_workforce_technician_avail_date"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    available_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE", nullable=False)  # AVAILABLE / UNAVAILABLE / ON_LEAVE


class TechnicianShift(Base, Timestamped):
    __tablename__ = "workforce_technician_shifts"
    __table_args__ = (UniqueConstraint("technician_id", "day_of_week", name="uq_workforce_technician_shift_day"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False, index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon .. 6=Sun
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class TechnicianStatusLog(Base):
    __tablename__ = "workforce_technician_status_log"
    __table_args__ = (Index("ix_workforce_technician_status_log", "technician_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_tenants.id"), index=True, nullable=False)
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_technicians.id"), nullable=False)
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="API", nullable=False)  # API / MOBILE / SYSTEM / SHIFT
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
