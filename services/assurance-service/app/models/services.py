"""Service catalogue: definitions, components, dependencies, owners, topology,
impact rules."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class ServiceDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_definitions"
    __table_args__ = (UniqueConstraint("code", name="uq_ass_service_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    criticality: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="STANDARD", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    owner_team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    documentation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ServiceComponent(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_components"
    __table_args__ = (UniqueConstraint("service_id", "name", name="uq_ass_service_component"),)

    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    component_type: Mapped[str] = mapped_column(String(40), default="APPLICATION", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ServiceDependency(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_dependencies"
    __table_args__ = (UniqueConstraint("service_id", "depends_on_id", name="uq_ass_service_dependency"),)

    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    depends_on_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    dependency_type: Mapped[str] = mapped_column(String(40), default="HTTP", nullable=False)
    critical: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ServiceOwner(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_owners"
    __table_args__ = (UniqueConstraint("service_id", "team", name="uq_ass_service_owner"),)

    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    team: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="PRIMARY", nullable=False)  # PRIMARY|BACKUP
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ServiceTopology(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_topology"
    __table_args__ = (UniqueConstraint("service_id", "version", name="uq_ass_service_topology_version"),)

    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    edges: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{source,target,type}]
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ServiceCustomerImpactRule(Base, Timestamped, UuidPk):
    __tablename__ = "ass_service_customer_impact_rules"
    __table_args__ = (UniqueConstraint("service_id", "impact_kind", name="uq_ass_service_impact_rule"),)

    service_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    impact_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    impact_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    estimated_subscriber_scale: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
