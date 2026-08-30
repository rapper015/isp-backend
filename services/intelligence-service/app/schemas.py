"""Pydantic request/response schemas for the Intelligence Service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---- Data foundation ----
class ContractIn(BaseModel):
    event_name: str
    version: str = "v1"
    contract_schema: dict = {}
    required_fields: list = []
    optional_fields: list = []
    pii_fields: list = []
    producer: str = "unknown-service"
    owner: Optional[str] = None
    retention_days: int = 365


class DatasetIn(BaseModel):
    code: str
    name: Optional[str] = None
    contracts: list = []
    criteria: dict = {}


class IngestEventIn(BaseModel):
    event_id: Optional[str] = None
    event_type: str
    tenant_id: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    occurred_at: Optional[datetime] = None
    producer: Optional[str] = None
    payload: dict = {}


# ---- Features ----
class FeatureIn(BaseModel):
    name: str
    version: str = "v1"
    domain_owner: Optional[str] = None
    source_contract: str
    entity_key: str = "customer"
    data_type: str = "FLOAT"
    freshness_seconds: int = 3600
    pii_class: str = "NONE"
    valid_range: dict = {}
    default_value: Optional[float] = None
    availability: str = "TRAINING_AND_SERVING"


# ---- MLOps ----
class TrainingConfigIn(BaseModel):
    model_code: str
    snapshot_id: uuid.UUID
    algorithm: str = "WEIGHTED_LOGIT"
    feature_names: list = []
    parameters: dict = {}
    decision_threshold: float = 0.5
    feature_set_version: Optional[str] = None
    use_case: str = "CHURN"
    name: Optional[str] = None
    split_scheme: str = "TIME_BASED"
    source_revision: Optional[str] = None
    owner: Optional[str] = None
    purpose: Optional[str] = None
    applicable_scope: str = "GLOBAL_BASELINE"


class DeployIn(BaseModel):
    environment: str
    traffic_percent: int = 100


class MonitorIn(BaseModel):
    metric_type: str
    value: float
    detail: dict = {}
    alert: bool = False


# ---- Fraud ----
class FraudEvalIn(BaseModel):
    subject_type: str = "subscriber"
    subject: str
    record: dict = {}
    model_score: Optional[float] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None


class FraudDecisionIn(BaseModel):
    decision: str
    reason: Optional[str] = None


class FraudActionIn(BaseModel):
    action_type: str
    target_service: str
    rationale: Optional[str] = None


# ---- Churn ----
class ChurnScoreIn(BaseModel):
    customer_ref: str
    service_ref: Optional[str] = None
    horizon_days: int = 30
    model_code: str = "churn_baseline_30d"


class RetentionTrackIn(BaseModel):
    presented: bool = True
    consent: bool = True
    accepted: Optional[bool] = None
    outcome: Optional[str] = None
    experiment_id: Optional[str] = None


# ---- Maintenance / capacity ----
class FailurePredictionIn(BaseModel):
    asset_type: str
    asset_ref: str
    model_code: str = "maintenance_baseline"
    horizon_days: int = 14


class CapacityForecastIn(BaseModel):
    resource_type: str
    resource_ref: str
    utilization_series: list = []
    horizon_days: int = 30
    model_code: Optional[str] = None


# ---- Recommendations / remediation ----
class RecommendationIn(BaseModel):
    kind: str
    subject_type: str
    subject: str
    summary: str
    evidence: list = []
    autonomy_level: str = "L1"
    model_code: Optional[str] = None
    model_version: Optional[int] = None
    confidence: float = 0.0
    expected_impact: Optional[str] = None
    expires_hours: int = 72


class RemediationIntentIn(BaseModel):
    policy_code: str
    target_type: str
    target_ref: str
    payload: dict = {}
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None


class ApprovalIn(BaseModel):
    approver: str
    reason: Optional[str] = None


class KillSwitchIn(BaseModel):
    scope: str = "GLOBAL"
    enabled: bool = True
    reason: Optional[str] = None
