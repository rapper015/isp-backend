"""Operational service subscription. Replaces the minimal legacy 'Subscriber'
model; it is an external-reference model owned by OSS that joins CRM customer
identity with BSS plan/billing, AAA access and order history."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class ServiceSubscription(Base, Timestamped):
    __tablename__ = "oss_service_subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", "subscription_code", name="uq_oss_subscription_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    subscription_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING_ACTIVATION", nullable=False, index=True)
    # External references (cross bounded-context IDs; strings).
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    service_location_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    plan_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_account_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aaa_subscriber_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nas_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_references: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    activation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
