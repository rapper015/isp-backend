"""Fraud detection: rule + model signals, cases, evidence, decisions.

A signal/case never auto-suspends service — it flows through review and any
action becomes a recommendation / remediation intent owned by the domain
service."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.fraud import decide, eval_condition, score_signal, severity_for
from ..domain.exceptions import NotFoundError
from ..models import (FraudActionRecommendation, FraudCase, FraudDecision, FraudEvidence,
                      FraudRule, FraudSignal)
from ..state_machine import guarded
from .audit_service import audit, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def evaluate_rules(session: Session, *, tenant_id, subject_type: str, subject: str,
                   record: dict, correlation_id: str | None = None,
                   model_score: float | None = None, model_code: str | None = None,
                   model_version: int | None = None) -> list[FraudSignal]:
    rules = list(session.scalars(select(FraudRule).where(FraudRule.is_active.is_(True))))
    signals = []
    hits = []
    weights = []
    for rule in rules:
        if eval_condition(record, rule.condition):
            hits.append(rule.code)
            weights.append(rule.risk_weight)
    if not hits and model_score is None:
        return signals
    score, factors = score_signal(rule_hits=len(hits), rule_weights=weights,
                                  model_score=model_score)
    signal = FraudSignal(
        tenant_id=tenant_id, subject_type=subject_type, subject=subject,
        rule_code=",".join(hits) if hits else None, model_code=model_code,
        model_version=model_version, risk_score=score, severity=severity_for(score),
        confidence=min(1.0, 0.5 + score * 0.5), detection_time=_now(),
        evidence=[{"rule": code, "record_snapshot": {k: v for k, v in record.items()
                                                     if not isinstance(v, dict)}} for code in hits],
        factors=factors, state="OPEN", correlation_id=correlation_id)
    session.add(signal)
    session.flush()
    signals.append(signal)
    if score >= 0.4:
        outbox(session, "ai.fraud_signal_detected.v1", tenant_id, correlation_id,
               {"signal_id": str(signal.id), "subject": subject, "risk_score": score,
                "severity": signal.severity}, idempotency_key=f"fraud-signal:{signal.id}")
    return signals


def open_case(session: Session, *, tenant_id, subject_type: str, subject: str,
              signals: list[FraudSignal], correlation_id: str | None = None) -> FraudCase:
    score = max((s.risk_score for s in signals), default=0.0)
    severity = severity_for(score)
    case = FraudCase(tenant_id=tenant_id, subject_type=subject_type, subject=subject,
                     summary=f"{len(signals)} fraud signal(s) for {subject}",
                     risk_score=score, severity=severity, state="OPEN",
                     decision=decide(score, severity), opened_at=_now(),
                     correlation_id=correlation_id)
    session.add(case)
    session.flush()
    for signal in signals:
        session.add(FraudEvidence(tenant_id=tenant_id, case_id=case.id, signal_id=signal.id,
                                  evidence_type="SIGNAL",
                                  detail={"rule": signal.rule_code, "score": signal.risk_score},
                                  observed_at=signal.detection_time))
    session.flush()
    return case


def transition(session: Session, case_id: uuid.UUID, target: str, *, actor: str | None = None) -> FraudCase:
    case = session.get(FraudCase, case_id)
    if case is None:
        raise NotFoundError("fraud case not found")
    guarded("fraud_case", case.state, target)
    case.state = target
    if target == "CLOSED":
        case.closed_at = _now()
    audit(session, case.tenant_id, actor, f"fraud_case.{target.lower()}", resource_type="fraud_case",
          resource_id=case.id)
    return case


def decide_case(session: Session, case_id: uuid.UUID, *, decision: str, reason: str | None = None,
                actor: str | None = None) -> FraudCase:
    case = session.get(FraudCase, case_id)
    if case is None:
        raise NotFoundError("fraud case not found")
    case.decision = decision
    case.final_outcome = decision
    session.add(FraudDecision(tenant_id=case.tenant_id, case_id=case.id, decision=decision,
                              reason=reason, actor=actor, decided_at=_now()))
    session.flush()
    audit(session, case.tenant_id, actor, f"fraud_case.decision.{decision}", resource_type="fraud_case",
          resource_id=case.id)
    return case


def recommend_action(session: Session, case_id: uuid.UUID, *, action_type: str, target_service: str,
                     rationale: str | None = None) -> FraudActionRecommendation:
    case = session.get(FraudCase, case_id)
    if case is None:
        raise NotFoundError("fraud case not found")
    row = FraudActionRecommendation(tenant_id=case.tenant_id, case_id=case.id,
                                    action_type=action_type, target_service=target_service,
                                    rationale=rationale, state="OPEN")
    session.add(row)
    session.flush()
    return row
