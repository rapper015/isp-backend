"""AIOps: fraud, churn, maintenance, capacity, recommendations, remediation."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


# ---------------- Fraud ----------------
class FraudRule(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_rules"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_ai_fraud_rule"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(8), default="v1", nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    risk_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class FraudSignal(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_signals"
    __table_args__ = (Index("ix_ai_fraud_signal", "tenant_id", "subject", "detection_time"),
                      UniqueConstraint("tenant_id", "subject", "rule_code", "detection_time",
                                       name="uq_ai_fraud_signal"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)  # subscriber|customer|account|nas|reseller
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    detection_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    factors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FraudCase(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_cases"
    __table_args__ = (Index("ix_ai_fraud_case", "tenant_id", "state"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    decision: Mapped[str] = mapped_column(String(24), default="REVIEW", nullable=False)
    final_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FraudEvidence(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_evidence"
    __table_args__ = (Index("ix_ai_fraud_evidence", "case_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FraudDecision(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_decisions"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FraudActionRecommendation(Base, Timestamped, UuidPk):
    __tablename__ = "ai_fraud_action_recommendations"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. REVIEW|VERIFY_IDENTITY|ESCALATE|MONITOR
    target_service: Mapped[str] = mapped_column(String(80), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)


# ---------------- Churn ----------------
class ChurnScore(Base, Timestamped, UuidPk):
    __tablename__ = "ai_churn_scores"
    __table_args__ = (Index("ix_ai_churn", "tenant_id", "customer_ref", "horizon_days"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    customer_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    service_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    top_drivers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    feature_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(String(60), nullable=True)


class RetentionCandidate(Base, Timestamped, UuidPk):
    __tablename__ = "ai_retention_candidates"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    churn_score_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    customer_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(60), nullable=False)
    offer_presented: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    offer_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    incremental_impact: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------- Maintenance & capacity ----------------
class FailurePrediction(Base, Timestamped, UuidPk):
    __tablename__ = "ai_failure_predictions"
    __table_args__ = (Index("ix_ai_failure", "tenant_id", "asset_type", "asset_ref"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False)  # nas|olt|pon_port|router|ont|cpe
    asset_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_probability: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    degradation_risk: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommendation_type: Mapped[str] = mapped_column(String(32), default="INSPECT", nullable=False)
    model_code: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class CapacityForecast(Base, Timestamped, UuidPk):
    __tablename__ = "ai_capacity_forecasts"
    __table_args__ = (Index("ix_ai_capacity", "tenant_id", "resource_type", "resource_ref"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)  # pop|nas|vlan|ip_pool|port|bandwidth|olt_pon
    resource_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    forecast: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # points + values
    confidence_interval: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    model_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------- Recommendations & remediation ----------------
class Recommendation(Base, Timestamped, UuidPk):
    __tablename__ = "ai_recommendations"
    __table_args__ = (Index("ix_ai_recommendation", "tenant_id", "kind", "state"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # FRAUD|CHURN|MAINTENANCE|CAPACITY|OPTIMIZATION
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    model_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autonomy_level: Mapped[str] = mapped_column(String(4), default="L1", nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RemediationPolicy(Base, Timestamped, UuidPk):
    __tablename__ = "ai_remediation_policies"
    __table_args__ = (UniqueConstraint("code", name="uq_ai_remediation_policy"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(4), default="L2", nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    action_budget: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    rate_limit_per_hour: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    max_blast_radius: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tenant_scope: Mapped[str] = mapped_column(String(16), default="TENANT", nullable=False)
    preconditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    circuit_breaker: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    retry_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)


class KillSwitch(Base, Timestamped, UuidPk):
    __tablename__ = "ai_kill_switches"
    __table_args__ = (UniqueConstraint("scope", "tenant_id", name="uq_ai_kill_switch"),)

    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # GLOBAL|TENANT
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemediationIntent(Base, Timestamped, UuidPk):
    __tablename__ = "ai_remediation_intents"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_ai_remediation_idem"),
                      Index("ix_ai_remediation", "tenant_id", "state"))

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    policy_code: Mapped[str] = mapped_column(String(80), nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(4), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    budget_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemediationApproval(Base, Timestamped, UuidPk):
    __tablename__ = "ai_remediation_approvals"
    __table_args__ = (UniqueConstraint("intent_id", "approver", name="uq_ai_remediation_approval"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    approver: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RemediationStep(Base, Timestamped, UuidPk):
    __tablename__ = "ai_remediation_steps"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    step: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemediationOutcome(Base, Timestamped, UuidPk):
    __tablename__ = "ai_remediation_outcomes"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(24), default="SUCCESS", nullable=False)
    verification: Mapped[str | None] = mapped_column(String(24), nullable=True)
    rollback_performed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
