"""Deterministic root-cause correlation (Milestone 9 §36–39).

Evidence-based only. Temporal coincidence alone is never marked CONFIRMED; every
hypothesis keeps supporting + contradicting evidence and requires human
confirmation for CONFIRMED_ROOT_CAUSE."""
from __future__ import annotations

from dataclasses import dataclass, field

from .exceptions import RootCauseError

ALLOWED_STATES = ("OBSERVATION", "HYPOTHESIS", "LIKELY_CAUSE", "CONFIRMED_ROOT_CAUSE", "REJECTED_HYPOTHESIS")

TRANSITIONS = {
    "OBSERVATION": {"HYPOTHESIS", "REJECTED_HYPOTHESIS"},
    "HYPOTHESIS": {"LIKELY_CAUSE", "REJECTED_HYPOTHESIS", "CONFIRMED_ROOT_CAUSE"},
    "LIKELY_CAUSE": {"CONFIRMED_ROOT_CAUSE", "REJECTED_HYPOTHESIS"},
    "CONFIRMED_ROOT_CAUSE": set(),
    "REJECTED_HYPOTHESIS": set(),
}


@dataclass
class CorrelationScorer:
    """Deterministic evidence scoring for correlation suggestions."""

    time_proximity_seconds: float = 300.0
    topology_dependency_weight: float = 0.4
    shared_resource_weight: float = 0.3
    time_proximity_weight: float = 0.2
    correlation_id_weight: float = 0.1

    def score(self, *, time_proximity_ok: bool, topology: bool, shared_resource: bool,
              correlation_id: bool) -> dict:
        score = 0.0
        reasons = []
        if topology:
            score += self.topology_dependency_weight
            reasons.append("topology_dependency")
        if shared_resource:
            score += self.shared_resource_weight
            reasons.append("shared_resource")
        if time_proximity_ok:
            score += self.time_proximity_weight
            reasons.append("time_proximity")
        if correlation_id:
            score += self.correlation_id_weight
            reasons.append("correlation_id")
        return {"score": round(score, 3), "reasons": reasons}


def transition(state: str, target: str) -> None:
    if target not in TRANSITIONS.get(state, set()):
        raise RootCauseError(f"invalid root-cause transition {state} -> {target}")


def confirm_requires_evidence(hypothesis: dict, evidence_count: int) -> bool:
    """CONFIRMED_ROOT_CAUSE requires at least one supporting evidence item and no
    unaddressed contradicting evidence."""
    if evidence_count < 1:
        return False
    if hypothesis.get("contradicting_evidence"):
        return False
    return True


@dataclass
class ImpactEstimate:
    affected_services: list = field(default_factory=list)
    estimated_subscribers: int = 0
    confirmed_subscribers: int = 0
    business_customers: int = 0
    revenue_risk_ref: str | None = None
    open_ticket_count: int = 0
    estimated: bool = True

    def to_dict(self) -> dict:
        return {"affected_services": self.affected_services,
                "estimated_subscribers": self.estimated_subscribers,
                "confirmed_subscribers": self.confirmed_subscribers,
                "business_customers": self.business_customers,
                "revenue_risk_ref": self.revenue_risk_ref,
                "open_ticket_count": self.open_ticket_count,
                "estimated": self.estimated}
