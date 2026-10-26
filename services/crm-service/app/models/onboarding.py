"""Durable records for workflows that cross CRM service boundaries."""
import uuid

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .base import Timestamped


class CustomerOnboardingOperation(Base, Timestamped):
    """Recoverable customer onboarding state.

    The local CRM aggregate is committed before OSS is called. This record
    then provides the checkpoint required to safely retry an interrupted
    create-order or activation request.
    """

    __tablename__ = "crm_customer_onboarding_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_crm_customer_onboarding_key"),
        UniqueConstraint("customer_id", name="uq_crm_customer_onboarding_customer"),
        Index("ix_crm_customer_onboarding_tenant_state", "tenant_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="LOCAL_DATA_CREATED")
    failed_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    local_resources: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    order_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    activation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
