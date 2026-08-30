"""Pydantic request/response schemas for the Assurance Service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- Service catalogue ----------
class ServiceDefinitionIn(BaseModel):
    code: str
    name: str
    criticality: str = "MEDIUM"
    tier: str = "TIER_2"
    owner_team: str = "PLATFORM"
    status: str = "ACTIVE"


class ServiceDefinitionOut(ServiceDefinitionIn):
    id: uuid.UUID
    created_at: datetime


class ServiceDependencyIn(BaseModel):
    service_id: uuid.UUID
    depends_on_id: uuid.UUID


# ---------- SLI / SLO ----------
class SlIDefinitionIn(BaseModel):
    code: str
    name: str
    service_id: Optional[uuid.UUID] = None
    measurement_source: str = "collector"
    good_event_definition: str
    valid_event_definition: str
    query_expression: Optional[str] = None
    unit: str = "ratio"
    exclusions: list = []
    owner: Optional[str] = None


class SlIMeasurementIn(BaseModel):
    sli_code: str
    good: float = 0.0
    total: float = 0.0
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    quality: str = "VALID"
    excluded_good: float = 0.0
    excluded_total: float = 0.0
    source_ref: Optional[str] = None


class SloCreateIn(BaseModel):
    code: str
    name: Optional[str] = None
    service_id: Optional[uuid.UUID] = None
    sli_id: uuid.UUID
    objective: float = Field(gt=0, le=1)
    window_type: str = "ROLLING"
    window_seconds: int = 30 * 24 * 3600
    service_tier: str = "STANDARD"
    effective_from: Optional[datetime] = None
    error_budget_policy: dict = {}
    alert_thresholds: dict = {}
    owner: Optional[str] = None
    published: bool = False


class MaintenanceWindowIn(BaseModel):
    service_id: Optional[uuid.UUID] = None
    starts_at: datetime
    ends_at: datetime
    maintenance_type: str = "PLANNED"
    reason: Optional[str] = None
    owner: Optional[str] = None
    scope_kind: str = "SERVICE"
    scope_ref: Optional[str] = None
    sla_treatment: str = "EXCLUDE"
    alert_suppression: bool = True


# ---------- Alerts ----------
class AlertIngestIn(BaseModel):
    service: str
    alert_name: str
    severity: str = "MEDIUM"
    component: Optional[str] = None
    resource: Optional[str] = None
    labels: dict = {}
    impact: Optional[str] = None
    source: str = "collector"
    correlation_id: Optional[str] = None
    observed_at: Optional[datetime] = None


class AlertSilenceIn(BaseModel):
    match_labels: dict = {}
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = None


class AlertRouteIn(BaseModel):
    name: str
    match_labels: dict = {}
    channel: str = "NOC_DASHBOARD"
    recipients: list = []
    escalation_policy: dict = {}
    fallback_route: Optional[str] = None


# ---------- Incidents ----------
class IncidentCreateIn(BaseModel):
    title: str
    severity: str = "MEDIUM"
    source: str = "MANUAL"
    description: Optional[str] = None
    alert_id: Optional[uuid.UUID] = None
    is_major: bool = False


class IncidentTransitionIn(BaseModel):
    target: str
    detail: dict = {}


class ImpactEstimateIn(BaseModel):
    impact_kind: str
    estimated_subscribers: int = 0
    impact_ref: Optional[str] = None
    detail: dict = {}


class ImpactConfirmIn(BaseModel):
    impact_kind: str
    confirmed_subscribers: int = 0
    impact_ref: Optional[str] = None


class CommunicationIn(BaseModel):
    audience: str = "INTERNAL"
    message: str
    channel: str = "STATUS_PAGE"


class IncidentActionIn(BaseModel):
    action_type: str
    description: Optional[str] = None
    assigned_to: Optional[str] = None


class PostmortemCreateIn(BaseModel):
    incident_id: uuid.UUID
    summary: Optional[str] = None
    root_cause: Optional[str] = None


class PostmortemActionIn(BaseModel):
    title: str
    owner: Optional[str] = None
    due_at: Optional[datetime] = None


class HypothesisIn(BaseModel):
    hypothesis: str
    confidence: float = 0.0
    is_ai_suggestion: bool = False


class EvidenceIn(BaseModel):
    evidence_type: str
    evidence_ref: str
    supports: bool = True
    detail: dict = {}


class ConfirmRootCauseIn(BaseModel):
    confirmed_by: str


# ---------- KPIs ----------
class KpiDefinitionIn(BaseModel):
    code: str
    name: str
    business_meaning: Optional[str] = None
    owner: Optional[str] = None
    formula: str
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    data_sources: list = []
    dimensions: list = []
    unit: str = "number"
    freshness_seconds: int = 900


class KpiMeasurementIn(BaseModel):
    kpi_code: str
    period_key: str
    value: float
    quality: str = "FRESH"
    dimensions: dict = {}
    measured_at: Optional[datetime] = None


class KpiTargetIn(BaseModel):
    kpi_code: str
    target: float
    direction: str = "ABOVE"
    target_key: str = "DEFAULT"


# ---------- Synthetic ----------
class SyntheticCheckIn(BaseModel):
    code: str
    kind: str
    target: Optional[str] = None
    frequency_seconds: int = 300
    timeout_seconds: int = 10
    tags: list = []


class SyntheticResultIn(BaseModel):
    check_code: str
    result: str = "PASS"
    latency_ms: float = 0.0
    detail: dict = {}


# ---------- Telemetry / observations ----------
class NetworkObservationIn(BaseModel):
    device_ref: str
    check_type: str
    status: str = "UNKNOWN"
    latency_ms: Optional[float] = None
    metrics: dict = {}
    source: str = "collector"


# ---------- Generic ----------
class Ok(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
