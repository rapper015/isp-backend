"""Relational franchise resource entitlements.

Resource entities remain owned by their domain services.  CRM owns the
organizational entitlement that allows a franchise to use those resources.
"""
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .base import Timestamped


class FranchisePackageAssignment(Base, Timestamped):
    __tablename__ = "crm_franchise_package_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "franchise_id", "package_id", name="uq_crm_franchise_package_assignment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), nullable=False, index=True)
    franchise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_franchises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    package_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FranchiseNasAssignment(Base, Timestamped):
    __tablename__ = "crm_franchise_nas_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "franchise_id", "nas_id", name="uq_crm_franchise_nas_assignment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), nullable=False, index=True)
    franchise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_franchises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nas_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FranchiseIpPoolAssignment(Base, Timestamped):
    __tablename__ = "crm_franchise_ip_pool_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "franchise_id", "ip_pool_id", name="uq_crm_franchise_ip_pool_assignment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), nullable=False, index=True)
    franchise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_franchises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
