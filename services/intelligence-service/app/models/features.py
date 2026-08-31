"""Versioned feature definitions + offline/online feature values."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class FeatureDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ai_feature_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_ai_feature_def"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    domain_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_contract: Mapped[str] = mapped_column(String(160), nullable=False)
    transformation_version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    entity_key: Mapped[str] = mapped_column(String(60), nullable=False)  # customer|subscriber|device|nas|pop|tenant
    data_type: Mapped[str] = mapped_column(String(16), default="FLOAT", nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    pii_class: Mapped[str] = mapped_column(String(16), default="NONE", nullable=False)
    valid_range: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    default_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    missing_behavior: Mapped[str] = mapped_column(String(24), default="DEFAULT", nullable=False)
    availability: Mapped[str] = mapped_column(String(24), default="TRAINING_AND_SERVING", nullable=False)
    expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FeatureValue(Base, Timestamped, UuidPk):
    """Offline feature value with event-time for point-in-time correctness."""

    __tablename__ = "ai_feature_values"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_ref", "feature_name", "event_time", "version",
                         name="uq_ai_feature_value"),
        Index("ix_ai_feature_lookup", "tenant_id", "entity_ref", "feature_name", "event_time"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    feature_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    str_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quality: Mapped[str] = mapped_column(String(16), default="FRESH", nullable=False)


class OnlineFeatureValue(Base, Timestamped, UuidPk):
    """Online (latest) feature value, refreshed by the pipeline / Redis cache."""

    __tablename__ = "ai_online_feature_values"
    __table_args__ = (UniqueConstraint("tenant_id", "entity_ref", "feature_name", name="uq_ai_online_feature"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    feature_name: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    str_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), default="FRESH", nullable=False)
