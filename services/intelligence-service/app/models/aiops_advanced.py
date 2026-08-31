"""Intelligence aiops advanced models (Master Spec Batch 8h).

Covers: 731 network digital twin, 739 autonomous scaling, 861 autonomous
pricing, 871 business digital twin, 883 upsell engine, 886 voice assistant,
888 sentiment response, 898 digital workforce.
"""
import uuid

from sqlalchemy import Boolean, Float, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped, UuidPk
from ..database import Base


class NetworkTwin(UuidPk, Base, Timestamped):
    """Network Digital Twin (731): virtual network replica."""
    __tablename__ = "ai_network_twin"
    __table_args__ = (UniqueConstraint("tenant_id", "twin_name", name="uq_ai_network_twin"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    twin_name: Mapped[str] = mapped_column(String(160), nullable=False)
    topology: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class ScalingAction(UuidPk, Base, Timestamped):
    """Autonomous Scaling (739): AI-driven scaling decisions."""
    __tablename__ = "ai_scaling_action"
    __table_args__ = (Index("ix_ai_scaling_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    service: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(20), default="SCALE_UP")  # SCALE_UP | SCALE_DOWN
    reason: Mapped[str | None] = mapped_column(String(300))


class PricingChange(UuidPk, Base, Timestamped):
    """Autonomous Pricing (861): dynamic AI pricing engine."""
    __tablename__ = "ai_pricing_change"
    __table_args__ = (Index("ix_ai_pricing_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    product: Mapped[str] = mapped_column(String(120), nullable=False)
    old_price: Mapped[float] = mapped_column(Float, default=0.0)
    new_price: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(String(300))


class BusinessTwin(UuidPk, Base, Timestamped):
    """Business Digital Twin (871): virtual business simulation."""
    __tablename__ = "ai_business_twin"
    __table_args__ = (UniqueConstraint("tenant_id", "twin_name", name="uq_ai_business_twin"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    twin_name: Mapped[str] = mapped_column(String(160), nullable=False)
    scenario: Mapped[str] = mapped_column(String(300), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class UpsellSuggestion(UuidPk, Base, Timestamped):
    """Upsell Engine (883): suggest upgrades."""
    __tablename__ = "ai_upsell_suggestion"
    __table_args__ = (Index("ix_ai_upsell_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    product: Mapped[str] = mapped_column(String(160), nullable=False)
    rationale: Mapped[str | None] = mapped_column(String(300))


class VoiceInteraction(UuidPk, Base, Timestamped):
    """Voice Assistant (886): voice-based support."""
    __tablename__ = "ai_voice_interaction"
    __table_args__ = (Index("ix_ai_voice_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    query: Mapped[str] = mapped_column(String(400), nullable=False)
    response: Mapped[str] = mapped_column(String(400), nullable=False)


class SentimentResponse(UuidPk, Base, Timestamped):
    """Sentiment Response (888): respond based on sentiment."""
    __tablename__ = "ai_sentiment_response"
    __table_args__ = (Index("ix_ai_sentiment_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    sentiment: Mapped[str] = mapped_column(String(20), default="NEUTRAL")
    action: Mapped[str] = mapped_column(String(300), nullable=False)


class WorkforceTask(UuidPk, Base, Timestamped):
    """Digital Workforce (898): fully AI workforce automation."""
    __tablename__ = "ai_workforce_task"
    __table_args__ = (Index("ix_ai_workforce_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    task_name: Mapped[str] = mapped_column(String(160), nullable=False)
    automation_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="AUTOMATED")
