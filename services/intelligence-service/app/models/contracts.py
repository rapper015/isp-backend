"""Data contracts, governed ingestion, datasets, quality, lineage."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class DataContract(Base, Timestamped, UuidPk):
    """Versioned contract for a trusted domain event consumed by the AI layer."""

    __tablename__ = "ai_data_contracts"
    __table_args__ = (UniqueConstraint("event_name", "version", name="uq_ai_contract"),)

    event_name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    required_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    optional_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    pii_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    producer: Mapped[str] = mapped_column(String(80), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    compatibility_rule: Mapped[str] = mapped_column(String(24), default="BACKWARD", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class RawEvent(Base, Timestamped, UuidPk):
    """Immutable raw record ingested from the event bus (append-only)."""

    __tablename__ = "ai_raw_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_ai_raw_event_id"),
        Index("ix_ai_raw_contract_time", "contract", "event_time"),
        Index("ix_ai_raw_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    contract: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    producer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="VALID", nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalyticalRecord(Base, Timestamped, UuidPk):
    """Normalized analytical record derived from a raw event."""

    __tablename__ = "ai_analytical_records"
    __table_args__ = (Index("ix_ai_analytical_entity", "entity_type", "entity_ref", "event_time"),
                      Index("ix_ai_analytical_contract", "contract"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    contract: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    normalized: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="event", nullable=False)


class DatasetSnapshot(Base, Timestamped, UuidPk):
    __tablename__ = "ai_dataset_snapshots"
    __table_args__ = (UniqueConstraint("code", name="uq_ai_dataset_code"),
                      Index("ix_ai_dataset_tenant", "tenant_id"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contract_filter: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    criteria: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DataQualityCheck(Base, Timestamped, UuidPk):
    __tablename__ = "ai_data_quality_checks"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    contract: Mapped[str] = mapped_column(String(160), nullable=False)
    check_type: Mapped[str] = mapped_column(String(60), nullable=False)  # completeness|freshness|schema|uniqueness|validity
    result: Mapped[str] = mapped_column(String(8), default="PASS", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PipelineRun(Base, Timestamped, UuidPk):
    __tablename__ = "ai_pipeline_runs"
    __table_args__ = (Index("ix_ai_pipeline_name", "pipeline"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    pipeline: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="RUNNING", nullable=False)
    counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LineageLink(Base, Timestamped, UuidPk):
    """Source event -> feature -> model -> prediction -> recommendation lineage."""

    __tablename__ = "ai_lineage_links"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    from_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    to_type: Mapped[str] = mapped_column(String(40), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    relation: Mapped[str] = mapped_column(String(40), default="DERIVED_FROM", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ConsentRecord(Base, Timestamped, UuidPk):
    __tablename__ = "ai_consent_records"
    __table_args__ = (UniqueConstraint("tenant_id", "entity_ref", "purpose", name="uq_ai_consent"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="tenant", nullable=False)
