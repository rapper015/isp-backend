"""MLOps lifecycle: training runs, model registry, deployments, monitoring."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class TrainingRun(Base, Timestamped, UuidPk):
    __tablename__ = "ai_training_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_ai_training_run"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    feature_set_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(80), nullable=True)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="QUEUED", nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    split_scheme: Mapped[str] = mapped_column(String(24), default="TIME_BASED", nullable=False)
    leakage_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelCard(Base, Timestamped, UuidPk):
    __tablename__ = "ai_model_cards"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    known_limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    intended_use: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    training_window: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fairness_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)


class MlModel(Base, Timestamped, UuidPk):
    """Machine-learning model asset (registry entry). Never a pickle."""

    __tablename__ = "ai_model_registry"
    __table_args__ = (UniqueConstraint("model_code", "version", name="uq_ai_model_version"),
                      Index("ix_ai_model_tenant", "tenant_id"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    use_case: Mapped[str] = mapped_column(String(32), nullable=False)  # FRAUD|CHURN|MAINTENANCE|CAPACITY
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    feature_names: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    feature_set_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    training_window: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evaluation_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    baseline_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    decision_threshold: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    calibration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    explainability_method: Mapped[str] = mapped_column(String(40), default="WEIGHTS", nullable=False)
    artifact: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applicable_scope: Mapped[str] = mapped_column(String(24), default="GLOBAL_BASELINE", nullable=False)
    approval_status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    deployment_status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_target: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)


class ModelDeployment(Base, Timestamped, UuidPk):
    __tablename__ = "ai_model_deployments"
    __table_args__ = (UniqueConstraint("model_id", "environment", name="uq_ai_model_deploy"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)  # SHADOW|CANARY|PRODUCTION
    traffic_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ModelMonitor(Base, Timestamped, UuidPk):
    __tablename__ = "ai_model_monitoring"
    __table_args__ = (Index("ix_ai_model_monitor", "model_id", "metric_type", "window_start"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)  # drift|prediction_distribution|latency|error_rate|calibration|completeness
    value: Mapped[float] = mapped_column(Float, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
