"""Milestone 3 network-control API (mounted under /api/aaa).

All endpoints use internal-service authentication and tenant scoping, matching
the existing AAA conventions. No endpoint accepts arbitrary RouterOS commands;
device changes go through the typed operations allowlist and managed-object
apply/verify/reconcile endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import (
    ActiveSession,
    AuditLog,
    BandwidthProfile,
    ControlAction,
    Credential,
    EnforcementAction,
    FairUsagePolicy,
    FupCounter,
    Nas,
    NasCredential,
    NetworkPolicy,
    NetworkPolicyVersion,
    PolicyDecision,
    PolicyDriftRecord,
    PolicyOverride,
    QosProfile,
    RouterReadinessReport,
    SubscriberPolicyAssignment,
    Tenant,
    TrafficClass,
)
from ..routeros import FakeRouterOSAdapter
from ..security import internal_service_auth
from ..services import audit as audit_svc
from ..services import correlation, outbox
from .control_actions import cancel as cancel_action
from .control_actions import create_control_action, record_outcome, retry as retry_action
from .enums import POLICY_STATES
from .fup import apply_topup, evaluate_fup, record_threshold_event, reset_cycle, usage_bytes
from .ip_identity import ip_history, regulatory_lookup, search_identity
from .policy_engine import PolicyFacts, evaluate_policy
from .qos import compile_qos_profile, is_managed, validate_managed_objects
from .radius_compiler import compile_radius_attributes, validate_policy_body
from .reconciliation import classify_nas_sessions
from .routeros_control import RouterOSControl, ProhibitedOperationError, build_winbox_guide, run_readiness_check
from .schemas import (
    BandwidthProfileCreate,
    ControlActionCreate,
    ControlOutcome,
    ExplainRequest,
    FupPolicyCreate,
    FupReset,
    FupTopUp,
    ManagedApply,
    ManagedRead,
    OverrideCreate,
    PolicyAssign,
    PolicyCreate,
    PolicySchedule,
    PolicyVersionCreate,
    QosProfileCreate,
    ReconcileRequest,
    TrafficClassCreate,
)
from .session_registry import classify_stale, detect_orphans, record_timeline, timeline as session_timeline
from . import control_actions

router = APIRouter(prefix="/api/aaa", dependencies=[Depends(internal_service_auth)])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _tenant(session: Session, tenant_id) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "tenant not found")
    return tenant


def _tenant_item(session: Session, model, item_id, tenant_id, label: str):
    item = session.scalar(select(model).where(model.id == item_id, model.tenant_id == tenant_id))
    if item is None:
        raise HTTPException(404, f"{label} not found")
    return item


def _nas(session: Session, nas_id, tenant_id) -> Nas:
    return _tenant_item(session, Nas, nas_id, tenant_id, "NAS")


def _audit(session: Session, tenant_id, action: str, target: str, detail: dict, correlation_id: str | None = None) -> str:
    request_id = correlation(correlation_id)
    audit_svc(session, tenant_id, action, target, request_id, detail)
    return request_id


def _policy_version(session: Session, tenant_id, policy_id, version: int) -> NetworkPolicyVersion:
    item = session.scalar(
        select(NetworkPolicyVersion).where(NetworkPolicyVersion.tenant_id == tenant_id, NetworkPolicyVersion.policy_id == policy_id, NetworkPolicyVersion.version == version)
    )
    if item is None:
        raise HTTPException(404, "policy version not found")
    return item


def _transition_version(session: Session, item: NetworkPolicyVersion, target: str, tenant_id, actor: str = "system") -> NetworkPolicyVersion:
    transitions = {
        "DRAFT": {"UNDER_REVIEW", "DISABLED", "ARCHIVED"},
        "UNDER_REVIEW": {"APPROVED", "DRAFT", "DISABLED"},
        "APPROVED": {"SCHEDULED", "ACTIVE", "DISABLED"},
        "SCHEDULED": {"ACTIVE", "APPROVED", "DISABLED"},
        "ACTIVE": {"DISABLED", "SUPERSEDED", "ARCHIVED"},
        "DISABLED": {"DRAFT", "ARCHIVED"},
    }
    if target not in transitions.get(item.state, set()):
        raise HTTPException(422, f"invalid policy version transition {item.state} -> {target}")
    item.state = target
    _audit(session, tenant_id, f"policy.version.{target.lower()}", str(item.id), {"policy_id": str(item.policy_id), "version": item.version, "actor": actor})
    return item


# ===========================================================================
# Policies
# ===========================================================================

@router.post("/policies", status_code=201)
def create_policy(payload: PolicyCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    errors = validate_policy_body(payload.body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    existing = session.scalar(select(NetworkPolicy).where(NetworkPolicy.tenant_id == payload.tenant_id, NetworkPolicy.code == payload.code))
    if existing is not None:
        raise HTTPException(409, "policy code already exists")
    policy = NetworkPolicy(tenant_id=payload.tenant_id, code=payload.code, name=payload.name, description=payload.description, state="DRAFT")
    session.add(policy)
    session.flush()
    version = NetworkPolicyVersion(
        tenant_id=payload.tenant_id,
        policy_id=policy.id,
        version=1,
        state="DRAFT",
        effective_from=payload.effective_from,
        body=payload.body,
    )
    session.add(version)
    session.flush()
    policy.current_version_id = version.id
    _audit(session, payload.tenant_id, "policy.created", str(policy.id), {"code": payload.code})
    session.commit()
    return {"id": str(policy.id), "code": policy.code, "state": policy.state, "current_version": version.version}


@router.get("/policies")
def list_policies(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "name": item.name, "state": item.state, "current_version_id": str(item.current_version_id) if item.current_version_id else None}
        for item in session.scalars(select(NetworkPolicy).where(NetworkPolicy.tenant_id == tenant_id).order_by(NetworkPolicy.created_at))
    ]


@router.post("/policies/{policy_id}/versions", status_code=201)
def create_policy_version(policy_id: uuid.UUID, payload: PolicyVersionCreate, session: Session = Depends(db)):
    policy = _tenant_item(session, NetworkPolicy, policy_id, payload.tenant_id, "policy")
    errors = validate_policy_body(payload.body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    last = session.scalar(select(NetworkPolicyVersion).where(NetworkPolicyVersion.policy_id == policy.id).order_by(NetworkPolicyVersion.version.desc()).limit(1))
    version = last.version + 1 if last else 1
    item = NetworkPolicyVersion(tenant_id=policy.tenant_id, policy_id=policy.id, version=version, state="DRAFT", effective_from=payload.effective_from, body=payload.body, notes=payload.notes, created_by=payload.actor)
    session.add(item)
    _audit(session, payload.tenant_id, "policy.version.created", str(item.id), {"policy_id": str(policy.id), "version": version, "actor": payload.actor})
    session.commit()
    return {"id": str(item.id), "policy_id": str(policy.id), "version": version, "state": "DRAFT"}


@router.get("/policies/{policy_id}/versions/{version}")
def get_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    return {"id": str(item.id), "policy_id": str(policy_id), "version": version, "state": item.state, "body": item.body, "effective_from": item.effective_from, "effective_to": item.effective_to, "created_at": item.created_at}


@router.post("/policies/{policy_id}/versions/{version}/validate")
def validate_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    errors = validate_policy_body(item.body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return {"valid": True, "policy_id": str(policy_id), "version": version}


@router.post("/policies/{policy_id}/versions/{version}/preview")
def preview_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    reply = compile_radius_attributes(item.body)
    return {"policy_id": str(policy_id), "version": version, "radius_attributes": reply}


@router.post("/policies/{policy_id}/versions/{version}/submit")
def submit_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    _transition_version(session, item, "UNDER_REVIEW", tenant_id)
    session.commit()
    return {"id": str(item.id), "state": item.state}


@router.post("/policies/{policy_id}/versions/{version}/approve")
def approve_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    _transition_version(session, item, "APPROVED", tenant_id)
    session.commit()
    return {"id": str(item.id), "state": item.state}


@router.post("/policies/{policy_id}/versions/{version}/schedule")
def schedule_policy_version(policy_id: uuid.UUID, version: int, payload: PolicySchedule, session: Session = Depends(db)):
    item = _policy_version(session, payload.tenant_id, policy_id, version)
    _transition_version(session, item, "SCHEDULED", payload.tenant_id, payload.actor)
    item.effective_from = payload.effective_from
    session.commit()
    return {"id": str(item.id), "state": item.state, "effective_from": item.effective_from}


@router.post("/policies/{policy_id}/versions/{version}/activate")
def activate_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    _transition_version(session, item, "ACTIVE", tenant_id)
    # Supersede other active versions of the same policy.
    for other in session.scalars(select(NetworkPolicyVersion).where(NetworkPolicyVersion.policy_id == policy_id, NetworkPolicyVersion.state == "ACTIVE", NetworkPolicyVersion.id != item.id)):
        other.state = "SUPERSEDED"
    policy = session.get(NetworkPolicy, policy_id)
    policy.current_version_id = item.id
    policy.state = "ACTIVE"
    _audit(session, tenant_id, "policy.activated", str(item.id), {"policy_id": str(policy_id), "version": version})
    session.commit()
    return {"id": str(item.id), "state": item.state, "policy_state": policy.state}


@router.post("/policies/{policy_id}/versions/{version}/disable")
def disable_policy_version(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    item = _policy_version(session, tenant_id, policy_id, version)
    _transition_version(session, item, "DISABLED", tenant_id)
    policy = session.get(NetworkPolicy, policy_id)
    if policy.current_version_id == item.id:
        policy.state = "DISABLED"
    session.commit()
    return {"id": str(item.id), "state": item.state}


@router.post("/policies/{policy_id}/versions/{version}/rollback")
def rollback_policy(policy_id: uuid.UUID, version: int, tenant_id: uuid.UUID, session: Session = Depends(db)):
    target = _policy_version(session, tenant_id, policy_id, version)
    policy = session.get(NetworkPolicy, policy_id)
    policy.current_version_id = target.id
    policy.state = "ACTIVE"
    target.state = "ACTIVE"
    _audit(session, tenant_id, "policy.rollback", str(policy.id), {"target_version": version})
    session.commit()
    return {"id": str(policy.id), "current_version": version}


@router.post("/subscribers/{subscriber_id}/policy-assignment")
def assign_policy(subscriber_id: uuid.UUID, payload: PolicyAssign, session: Session = Depends(db)):
    version = session.get(NetworkPolicyVersion, payload.policy_version_id)
    if version is None or version.tenant_id != payload.tenant_id:
        raise HTTPException(404, "policy version not found")
    assignment = session.scalar(select(SubscriberPolicyAssignment).where(SubscriberPolicyAssignment.tenant_id == payload.tenant_id, SubscriberPolicyAssignment.subscriber_id == subscriber_id, SubscriberPolicyAssignment.source == payload.source))
    if assignment is None:
        assignment = SubscriberPolicyAssignment(tenant_id=payload.tenant_id, subscriber_id=subscriber_id, policy_version_id=version.id, source=payload.source)
        session.add(assignment)
    else:
        assignment.policy_version_id = version.id
        assignment.active = True
    _audit(session, payload.tenant_id, "policy.assigned", str(subscriber_id), {"policy_version_id": str(version.id), "source": payload.source, "actor": payload.actor})
    session.commit()
    return {"subscriber_id": str(subscriber_id), "policy_version_id": str(version.id), "source": payload.source}


@router.post("/subscribers/{subscriber_id}/overrides", status_code=201)
def add_override(subscriber_id: uuid.UUID, payload: OverrideCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    errors = validate_policy_body(payload.body)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    override = PolicyOverride(tenant_id=payload.tenant_id, subscriber_id=subscriber_id, kind="temporary", body=payload.body, reason=payload.reason, expires_at=payload.expires_at, active=True, created_by=payload.actor)
    session.add(override)
    _audit(session, payload.tenant_id, "policy.override.added", str(subscriber_id), {"override_id": str(override.id), "actor": payload.actor})
    session.commit()
    return {"id": str(override.id), "expires_at": override.expires_at}


@router.delete("/subscribers/{subscriber_id}/overrides/{override_id}")
def remove_override(subscriber_id: uuid.UUID, override_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    override = session.scalar(select(PolicyOverride).where(PolicyOverride.id == override_id, PolicyOverride.tenant_id == tenant_id, PolicyOverride.subscriber_id == subscriber_id))
    if override is None:
        raise HTTPException(404, "override not found")
    override.active = False
    _audit(session, tenant_id, "policy.override.removed", str(subscriber_id), {"override_id": str(override_id)})
    session.commit()
    return {"id": str(override_id), "active": False}


@router.post("/subscribers/{subscriber_id}/effective-policy/explain")
def explain_effective_policy(subscriber_id: uuid.UUID, payload: ExplainRequest, session: Session = Depends(db)):
    """Evaluate and explain the effective policy for a subscriber, persisting
    an immutable PolicyDecision."""
    tenant = _tenant(session, payload.tenant_id)
    facts = _build_facts(session, tenant, subscriber_id, payload.facts, payload.nas_id)
    result = evaluate_policy(facts)
    decision = PolicyDecision(
        tenant_id=tenant.id,
        subscriber_id=subscriber_id,
        customer_ref=facts.customer_ref,
        subscription_ref=facts.subscription_ref,
        session_ref=facts.session_ref,
        nas_id=facts.nas_id,
        policy_version_id=facts.policy_version_id,
        input_facts=_safe_facts(facts),
        fact_versions={},
        rules_evaluated=result.rules_evaluated,
        winning_rules=result.winning_rules,
        rejected_rules=result.rejected_rules,
        resulting_policy=result.policy,
        reason_code=result.reason_code,
        explanation=result.explanation,
        correlation_id=correlation(None),
        actor="explain",
    )
    session.add(decision)
    session.commit()
    return {
        "decision_id": str(decision.id),
        "reason_code": result.reason_code,
        "explanation": result.explanation,
        "policy": result.policy,
        "provenance": result.provenance,
        "radius_attributes": result.reply_attributes(),
        "rules_evaluated": result.rules_evaluated,
        "winning_rules": result.winning_rules,
        "rejected_rules": result.rejected_rules,
    }


def _build_facts(session: Session, tenant: Tenant, subscriber_id, facts_body: dict, nas_id=None) -> PolicyFacts:
    now = _now()
    default_policy = dict(tenant.policy.get("default_policy", {}))
    plan_policy: dict = {}
    policy_version_id = None
    assignment = session.scalar(select(SubscriberPolicyAssignment).where(SubscriberPolicyAssignment.tenant_id == tenant.id, SubscriberPolicyAssignment.subscriber_id == subscriber_id, SubscriberPolicyAssignment.active.is_(True)).order_by(SubscriberPolicyAssignment.created_at.desc()))
    if assignment is not None:
        version = session.get(NetworkPolicyVersion, assignment.policy_version_id)
        if version is not None and version.state in ("ACTIVE", "APPROVED", "SCHEDULED"):
            plan_policy = dict(version.body)
            policy_version_id = version.id
    override = session.scalar(select(PolicyOverride).where(PolicyOverride.tenant_id == tenant.id, PolicyOverride.subscriber_id == subscriber_id, PolicyOverride.active.is_(True)).order_by(PolicyOverride.created_at.desc()))
    temporary_override = dict(override.body) if override else None
    temporary_expired = bool(override and override.expires_at and override.expires_at < now)
    fup = session.scalar(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == tenant.id).order_by(FairUsagePolicy.created_at.desc()))
    fup_tier = evaluate_fup(session, tenant.id, subscriber_id, fup, now) if fup else None
    congestion_tier = facts_body.get("congestion_tier")
    addons = facts_body.get("addon_policies", [])
    subscriber_policy = facts_body.get("subscriber_policy")
    return PolicyFacts(
        tenant_id=tenant.id,
        subscriber_id=subscriber_id,
        customer_ref=facts_body.get("customer_ref"),
        subscription_ref=facts_body.get("subscription_ref"),
        session_ref=facts_body.get("session_ref"),
        nas_id=nas_id,
        policy_version_id=policy_version_id,
        default_policy=default_policy,
        plan_policy=plan_policy,
        addon_policies=addons,
        subscriber_policy=subscriber_policy,
        temporary_override=temporary_override,
        temporary_expired=temporary_expired,
        fup_tier=fup_tier,
        congestion_tier=congestion_tier,
        billing_suspended=bool(facts_body.get("billing_suspended")),
        oss_suspended=bool(facts_body.get("oss_suspended")),
        fraud=bool(facts_body.get("fraud")),
        admin_suspended=bool(facts_body.get("admin_suspended")),
        security_block=bool(facts_body.get("security_block")),
        regulatory_block=bool(facts_body.get("regulatory_block")),
        now=now,
    )


def _safe_facts(facts: PolicyFacts) -> dict:
    return {
        "subscriber_id": str(facts.subscriber_id) if facts.subscriber_id else None,
        "customer_ref": facts.customer_ref,
        "subscription_ref": facts.subscription_ref,
        "has_plan_policy": bool(facts.plan_policy),
        "has_temporary_override": facts.temporary_override is not None,
        "temporary_expired": facts.temporary_expired,
        "fup_tier": facts.fup_tier,
        "congestion_tier": facts.congestion_tier,
        "billing_suspended": facts.billing_suspended,
        "oss_suspended": facts.oss_suspended,
        "fraud": facts.fraud,
        "admin_suspended": facts.admin_suspended,
        "security_block": facts.security_block,
        "regulatory_block": facts.regulatory_block,
    }


# ===========================================================================
# Sessions
# ===========================================================================

def _session_tenant(session: Session, session_id, tenant_id) -> ActiveSession:
    item = session.scalar(select(ActiveSession).where(ActiveSession.id == session_id, ActiveSession.tenant_id == tenant_id))
    if item is None:
        raise HTTPException(404, "session not found")
    return item


@router.get("/network/sessions")
def list_network_sessions(tenant_id: uuid.UUID, status: str | None = None, nas_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    stmt = select(ActiveSession).where(ActiveSession.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ActiveSession.status == status)
    if nas_id:
        stmt = stmt.where(ActiveSession.nas_id == nas_id)
    rows = []
    for item in session.scalars(stmt.order_by(ActiveSession.started_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 200))):
        rows.append(_session_json(item))
    return rows


def _session_json(item: ActiveSession) -> dict:
    return {
        "id": str(item.id),
        "session_id": item.session_id,
        "username": item.username,
        "status": item.status,
        "nas_id": str(item.nas_id),
        "subscriber_id": str(item.subscriber_id) if item.subscriber_id else None,
        "framed_ip": item.framed_ip,
        "mac_address": item.mac_address,
        "started_at": item.started_at,
        "last_interim_at": item.last_interim_at,
        "input_octets": item.input_octets,
        "output_octets": item.output_octets,
        "policy_snapshot": item.policy_snapshot,
    }


@router.get("/network/sessions/search")
def search_sessions(tenant_id: uuid.UUID, username: str | None = None, ip: str | None = None, mac: str | None = None, session_id: str | None = None, limit: int = 50, session: Session = Depends(db)):
    return search_identity(session, tenant_id, username=username, ip=ip, mac=mac, session_id=session_id, limit=limit)


@router.get("/network/sessions/{session_id}")
def network_session_detail(session_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return _session_json(_session_tenant(session, session_id, tenant_id))


@router.get("/network/sessions/{session_id}/timeline")
def network_session_timeline(session_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    _session_tenant(session, session_id, tenant_id)
    return [{"event_type": item.event_type, "payload": item.payload, "correlation_id": item.correlation_id, "created_at": item.created_at} for item in session_timeline(session, tenant_id, session_id)]


@router.post("/network/sessions/{session_id}/disconnect", status_code=201)
def disconnect_session(session_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    tenant_id = uuid.UUID(payload["tenant_id"])
    active = _session_tenant(session, session_id, tenant_id)
    idem = payload.get("idempotency_key") or f"disconnect:{session_id}:{correlation(None)}"
    action = create_control_action(
        session,
        tenant_id,
        action_type="DISCONNECT",
        trigger=payload.get("trigger", "operator"),
        nas_id=active.nas_id,
        session_id=active.id,
        subscriber_id=active.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id, "User-Name": active.username, "Framed-IP-Address": active.framed_ip},
        requested_attributes={"Acct-Session-Id": active.session_id},
        idempotency_key=idem,
        actor=payload.get("actor", "system"),
    )
    active.status = "DISCONNECT_REQUESTED"
    record_timeline(session, tenant_id, active, "session.disconnect_requested", {"control_action_id": str(action.id), "correlation_id": action.correlation_id})
    session.commit()
    return {"id": str(action.id), "status": action.status, "strategy": action.strategy, "correlation_id": action.correlation_id}


@router.post("/network/subscribers/{subscriber_id}/disconnect-all", status_code=201)
def disconnect_all(subscriber_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    tenant_id = uuid.UUID(payload["tenant_id"])
    sessions = list(session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.subscriber_id == subscriber_id, ActiveSession.status.in_(["STARTING", "ACTIVE", "STALE", "ORPHANED"])).limit(100)))
    if not sessions:
        raise HTTPException(404, "no active sessions for subscriber")
    if not payload.get("approved", False):
        raise HTTPException(403, "bulk disconnect requires approval")
    results = []
    base = payload.get("idempotency_key") or correlation(None)
    for index, active in enumerate(sessions):
        action = create_control_action(
            session,
            tenant_id,
            action_type="DISCONNECT",
            trigger="bulk_disconnect",
            nas_id=active.nas_id,
            session_id=active.id,
            subscriber_id=subscriber_id,
            username=active.username,
            session_identifier={"Acct-Session-Id": active.session_id},
            requested_attributes={"Acct-Session-Id": active.session_id},
            idempotency_key=f"{base}:{index}",
            actor=payload.get("actor", "system"),
        )
        active.status = "DISCONNECT_REQUESTED"
        results.append({"control_action_id": str(action.id), "session_id": str(active.id)})
    session.commit()
    return {"subscriber_id": str(subscriber_id), "actions": results, "count": len(results)}


@router.post("/network/sessions/{session_id}/reapply", status_code=201)
def reapply_session(session_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    tenant_id = uuid.UUID(payload["tenant_id"])
    active = _session_tenant(session, session_id, tenant_id)
    attributes = compile_radius_attributes(payload.get("policy", {}))
    idem = payload.get("idempotency_key") or f"coa:{session_id}:{correlation(None)}"
    action = create_control_action(
        session,
        tenant_id,
        action_type="COA",
        trigger="policy_reapply",
        nas_id=active.nas_id,
        session_id=active.id,
        subscriber_id=active.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id, "Framed-IP-Address": active.framed_ip},
        requested_attributes=attributes,
        idempotency_key=idem,
        actor=payload.get("actor", "system"),
    )
    active.policy_snapshot = {"reapply": payload.get("policy", {})}
    record_timeline(session, tenant_id, active, "session.policy_reapply_requested", {"control_action_id": str(action.id), "strategy": action.strategy})
    session.commit()
    return {"id": str(action.id), "status": action.status, "strategy": action.strategy}


@router.post("/network/sessions/{session_id}/force-reauth", status_code=201)
def force_reauth(session_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    """Unsupported live IP/pool changes: persist desired policy, disconnect,
    let the subscriber re-authenticate with the new policy."""
    tenant_id = uuid.UUID(payload["tenant_id"])
    active = _session_tenant(session, session_id, tenant_id)
    idem = payload.get("idempotency_key") or f"reauth:{session_id}:{correlation(None)}"
    action = create_control_action(
        session,
        tenant_id,
        action_type="DISCONNECT",
        trigger="disconnect_and_reauth",
        nas_id=active.nas_id,
        session_id=active.id,
        subscriber_id=active.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Acct-Session-Id": active.session_id, "Framed-IP-Address": payload.get("new_ip")} if payload.get("new_ip") else {"Acct-Session-Id": active.session_id},
        idempotency_key=idem,
        actor=payload.get("actor", "system"),
    )
    active.status = "DISCONNECT_REQUESTED"
    record_timeline(session, tenant_id, active, "session.force_reauth", {"control_action_id": str(action.id), "desired_ip": payload.get("new_ip")})
    session.commit()
    return {"id": str(action.id), "strategy": "DISCONNECT_AND_REAUTHORIZE", "note": "subscriber must re-authenticate to receive the new IP policy"}


@router.post("/network/sessions/classify-stale")
def run_classify_stale(tenant_id: uuid.UUID, interim_threshold_seconds: int = 600, session: Session = Depends(db)):
    stale = classify_stale(session, tenant_id, interim_threshold_seconds)
    for item in stale:
        record_timeline(session, tenant_id, item, "session.marked_stale", {"reason": "interim update window missed"})
    session.commit()
    return {"stale_count": len(stale), "session_ids": [str(item.id) for item in stale]}


@router.post("/network/sessions/detect-orphans")
def run_detect_orphans(tenant_id: uuid.UUID, orphan_after_seconds: int = 3600, session: Session = Depends(db)):
    orphaned = detect_orphans(session, tenant_id, orphan_after_seconds)
    for item in orphaned:
        record_timeline(session, tenant_id, item, "session.orphaned", {"reason": "no stop or interim beyond threshold"})
    session.commit()
    return {"orphaned_count": len(orphaned), "session_ids": [str(item.id) for item in orphaned]}


# ===========================================================================
# Control actions
# ===========================================================================

@router.post("/control-actions", status_code=201)
def create_control(payload: ControlActionCreate, session: Session = Depends(db)):
    _nas(session, payload.nas_id, payload.tenant_id)
    try:
        action = create_control_action(
            session,
            payload.tenant_id,
            action_type=payload.action_type,
            trigger=payload.trigger,
            nas_id=payload.nas_id,
            session_id=payload.session_id,
            subscriber_id=payload.subscriber_id,
            username=payload.username,
            session_identifier=payload.session_identifier,
            requested_attributes=payload.requested_attributes,
            idempotency_key=payload.idempotency_key,
            actor=payload.actor,
            correlation_id=payload.correlation_id,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(action.id), "action_type": action.action_type, "status": action.status, "strategy": action.strategy, "idempotent": False}


@router.get("/control-actions")
def list_control_actions(tenant_id: uuid.UUID, status: str | None = None, action_type: str | None = None, limit: int = 100, session: Session = Depends(db)):
    stmt = select(ControlAction).where(ControlAction.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ControlAction.status == status)
    if action_type:
        stmt = stmt.where(ControlAction.action_type == action_type)
    return [
        {"id": str(item.id), "action_type": item.action_type, "status": item.status, "strategy": item.strategy, "trigger": item.trigger, "attempts": item.attempts, "latency_ms": item.latency_ms, "correlation_id": item.correlation_id, "created_at": item.created_at}
        for item in session.scalars(stmt.order_by(ControlAction.created_at.desc()).limit(min(max(limit, 1), 200)))
    ]


@router.get("/control-actions/{action_id}")
def control_action_detail(action_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    action = session.scalar(select(ControlAction).where(ControlAction.id == action_id, ControlAction.tenant_id == tenant_id))
    if action is None:
        raise HTTPException(404, "control action not found")
    return {
        "id": str(action.id),
        "action_type": action.action_type,
        "status": action.status,
        "strategy": action.strategy,
        "trigger": action.trigger,
        "session_identifier": action.session_identifier,
        "requested_attributes": action.requested_attributes,
        "attempts": action.attempts,
        "max_attempts": action.max_attempts,
        "sent_at": action.sent_at,
        "ack_at": action.ack_at,
        "nak_at": action.nak_at,
        "timeout_at": action.timeout_at,
        "latency_ms": action.latency_ms,
        "response": action.response,
        "correlation_id": action.correlation_id,
        "error": action.error,
    }


@router.post("/control-actions/{action_id}/outcome")
def control_outcome(action_id: uuid.UUID, payload: ControlOutcome, session: Session = Depends(db)):
    action = session.scalar(select(ControlAction).where(ControlAction.id == action_id, ControlAction.tenant_id == payload.tenant_id))
    if action is None:
        raise HTTPException(404, "control action not found")
    try:
        action = record_outcome(session, action_id, payload.outcome, payload.detail, payload.latency_ms)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(action.id), "status": action.status, "outcome": payload.outcome}


@router.post("/control-actions/{action_id}/retry")
def control_retry(action_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    action = session.scalar(select(ControlAction).where(ControlAction.id == action_id, ControlAction.tenant_id == tenant_id))
    if action is None:
        raise HTTPException(404, "control action not found")
    try:
        retry_action(session, action_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(action_id), "status": "PENDING"}


@router.post("/control-actions/{action_id}/cancel")
def control_cancel(action_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    action = session.scalar(select(ControlAction).where(ControlAction.id == action_id, ControlAction.tenant_id == tenant_id))
    if action is None:
        raise HTTPException(404, "control action not found")
    try:
        cancel_action(session, action_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(action_id), "status": "CANCELLED"}


# ===========================================================================
# RouterOS / managed configuration
# ===========================================================================

def _build_adapter(session: Session, nas: Nas):
    from ..nas_service import build_adapter as build

    credential = session.scalar(select(NasCredential).where(NasCredential.nas_id == nas.id, NasCredential.status == "active").order_by(NasCredential.created_at.desc()).limit(1))
    return build(nas, credential)


@router.post("/nas/{nas_id}/network-readiness")
def network_readiness(nas_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    nas = _nas(session, nas_id, tenant_id)
    adapter = _build_adapter(session, nas)
    report = run_readiness_check(adapter, nas, tenant_id)
    request_id = _audit(session, tenant_id, "router.readiness_checked", str(nas_id), {"status": report["status"]})
    persisted = RouterReadinessReport(tenant_id=tenant_id, nas_id=nas.id, status=report["status"], checks=report["checks"], winbox_guide=report["winbox_guide"], correlation_id=request_id)
    session.add(persisted)
    session.commit()
    return {"nas_id": str(nas_id), "status": report["status"], "checks": report["checks"], "winbox_guide": report["winbox_guide"], "report_id": str(persisted.id)}


@router.get("/nas/{nas_id}/network-setup-requirements")
def network_setup_requirements(nas_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    nas = _nas(session, nas_id, tenant_id)
    report = session.scalar(select(RouterReadinessReport).where(RouterReadinessReport.tenant_id == tenant_id, RouterReadinessReport.nas_id == nas_id).order_by(RouterReadinessReport.created_at.desc()).limit(1))
    if report is None:
        raise HTTPException(404, "run a readiness check first")
    return {"nas_id": str(nas_id), "status": report.status, "winbox_guide": report.winbox_guide}


@router.post("/nas/{nas_id}/managed-config/read")
def read_managed_config(payload: ManagedRead, session: Session = Depends(db)):
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    adapter = _build_adapter(session, nas)
    control = RouterOSControl(adapter)
    observed = {
        "queue_types": control.call("read_queue_types"),
        "queues": control.call("read_queues"),
        "queue_trees": control.call("read_queue_trees"),
        "mangle_rules": control.call("read_mangle_rules"),
        "address_lists": control.call("read_address_lists"),
    }
    return {"nas_id": str(nas_id), "observed": observed}


@router.post("/nas/{nas_id}/managed-config/diff")
def diff_managed_config(payload: ManagedApply, session: Session = Depends(db)):
    """Desired (from policy QoS objects) vs observed; reports drift without
    applying anything."""
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    adapter = _build_adapter(session, nas)
    control = RouterOSControl(adapter)
    errors = validate_managed_objects(payload.objects)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    observed = control.call("read_queue_types") + control.call("read_mangle_rules") + control.call("read_address_lists")
    desired_keys = {}
    for obj in payload.objects:
        key = f"{obj['kind']}:{obj.get('params', {}).get('name') or obj.get('name')}"
        desired_keys[key] = obj
    observed_keys = set()
    for item in observed:
        kind = "queue_type" if item.get("kind") else ("mangle_rule" if "chain" in item else "address_list")
        name = item.get("name") or item.get("list")
        observed_keys.add(f"{kind}:{name}")
    missing = sorted(set(desired_keys) - observed_keys)
    extra_unmanaged = sorted(k for k in observed_keys - set(desired_keys) if not is_managed(None))
    request_id = _audit(session, payload.tenant_id, "router.config_diff", str(nas.id), {"missing": len(missing), "extra": len(extra_unmanaged)})
    session.commit()
    return {"nas_id": str(nas.id), "missing": missing, "extra_unmanaged": extra_unmanaged, "applied": False, "correlation_id": request_id}


@router.post("/nas/{nas_id}/managed-config/apply")
def apply_managed_config(payload: ManagedApply, session: Session = Depends(db)):
    """Apply only validated, platform-managed QoS objects via the typed
    operations allowlist. Manual (unmanaged) configuration is never touched."""
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    adapter = _build_adapter(session, nas)
    control = RouterOSControl(adapter)
    errors = validate_managed_objects(payload.objects)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    applied: list[dict] = []
    op_by_kind = {
        "queue_type": "create_managed_queue_type",
        "queue_tree": "create_managed_queue_tree",
        "simple_queue": "create_managed_queue_type",
        "mangle_rule": "create_managed_mangle",
        "address_list": "create_managed_address_list",
    }
    for obj in payload.objects:
        operation = op_by_kind.get(obj["kind"])
        if operation is None:
            raise HTTPException(422, f"unsupported managed object kind {obj['kind']!r}")
        try:
            remote_id = control.call(operation, obj.get("params", {}))
            applied.append({"kind": obj["kind"], "name": obj.get("params", {}).get("name"), "remote_id": remote_id})
        except ProhibitedOperationError as error:
            raise HTTPException(422, str(error)) from error
    request_id = _audit(session, payload.tenant_id, "router.configuration_applied", str(nas.id), {"objects": len(applied), "policy_version_id": str(payload.policy_version_id)})
    outbox(session, "router.configuration_applied.v1", payload.tenant_id, request_id, {"nas_id": str(nas.id), "objects": len(applied), "policy_version_id": str(payload.policy_version_id)}, f"apply:{payload.nas_id}:{request_id}")
    session.commit()
    return {"nas_id": str(nas.id), "applied": applied, "correlation_id": request_id}


@router.post("/nas/{nas_id}/managed-config/verify")
def verify_managed_config(payload: ManagedApply, session: Session = Depends(db)):
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    adapter = _build_adapter(session, nas)
    control = RouterOSControl(adapter)
    desired_keys = {f"{obj['kind']}:{obj.get('params', {}).get('name') or obj.get('name')}" for obj in payload.objects}
    observed = control.call("read_queue_types") + control.call("read_mangle_rules") + control.call("read_address_lists")
    present = {f"{'queue_type' if item.get('kind') else 'mangle_rule' if 'chain' in item else 'address_list'}:{item.get('name') or item.get('list')}" for item in observed}
    missing = sorted(desired_keys - present)
    return {"nas_id": str(nas.id), "verified": not missing, "missing": missing}


@router.post("/nas/{nas_id}/managed-config/reconcile")
def reconcile_managed_config(payload: ManagedApply, session: Session = Depends(db)):
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    result = diff_managed_config(payload, session)
    request_id = _audit(session, payload.tenant_id, "router.reconciled", str(nas.id), {"missing": len(result["missing"])})
    for key in result["missing"]:
        session.add(PolicyDriftRecord(tenant_id=payload.tenant_id, nas_id=nas.id, drift_type="missing_managed_object", classification="REPAIRABLE", resource_kind=key.split(":")[0], resource_key=":".join(key.split(":")[1:]), detail={"object": key}, status="OPEN"))
    outbox(session, "router.drift_detected.v1", payload.tenant_id, request_id, {"nas_id": str(nas.id), "missing": result["missing"]}, f"drift:{payload.nas_id}:{request_id}")
    session.commit()
    return {"nas_id": str(nas.id), "missing": result["missing"], "applied": False, "drift_records": len(result["missing"]), "correlation_id": request_id}


@router.get("/nas/{nas_id}/policy-drift")
def list_policy_drift(nas_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "drift_type": item.drift_type, "classification": item.classification, "resource_kind": item.resource_kind, "resource_key": item.resource_key, "detail": item.detail, "status": item.status, "created_at": item.created_at}
        for item in session.scalars(select(PolicyDriftRecord).where(PolicyDriftRecord.tenant_id == tenant_id, PolicyDriftRecord.nas_id == nas_id).order_by(PolicyDriftRecord.created_at.desc()).limit(200))
    ]


@router.post("/network/reconcile")
def reconcile_network(payload: ReconcileRequest, session: Session = Depends(db)):
    nas = _nas(session, payload.nas_id, payload.tenant_id)
    result = classify_nas_sessions(session, payload.tenant_id, nas.id, set(payload.router_session_ids), suspended_subscriber_ids=set(payload.suspended_subscriber_ids))
    request_id = _audit(session, payload.tenant_id, "network.reconciled", str(nas.id), {"database_only": len(result["database_only"]), "router_only": len(result["router_only"]), "suspended_online": len(result["suspended_subscriber_online"])})
    session.commit()
    return {"nas_id": str(nas.id), **result, "correlation_id": request_id}


# ===========================================================================
# FUP
# ===========================================================================

@router.get("/fup/subscribers/{subscriber_id}/usage")
def fup_usage(subscriber_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    fup = session.scalar(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == tenant_id).order_by(FairUsagePolicy.created_at.desc()))
    if fup is None:
        raise HTTPException(404, "no FUP policy configured for tenant")
    input_octets, output_octets = usage_bytes(session, tenant_id, subscriber_id, _cycle(fup))
    tier = evaluate_fup(session, tenant_id, subscriber_id, fup)
    return {"subscriber_id": str(subscriber_id), "input_octets": input_octets, "output_octets": output_octets, "active_tier": tier, "cycle": fup.cycle}


def _cycle(fup) -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d") if fup.cycle == "daily" else now.strftime("%Y-%m")


@router.get("/fup/subscribers/{subscriber_id}/history")
def fup_history(subscriber_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"cycle": item.cycle, "input_octets": item.input_octets, "output_octets": item.output_octets, "active_tier": item.active_tier, "throttled": item.throttled, "topup_bytes": item.topup_bytes, "last_event_at": item.last_event_at}
        for item in session.scalars(select(FupCounter).where(FupCounter.tenant_id == tenant_id, FupCounter.subscriber_id == subscriber_id).order_by(FupCounter.cycle))
    ]


@router.post("/fup/subscribers/{subscriber_id}/reset")
def fup_reset(subscriber_id: uuid.UUID, payload: FupReset, session: Session = Depends(db)):
    fup = session.scalar(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == payload.tenant_id).order_by(FairUsagePolicy.created_at.desc()))
    if fup is None:
        raise HTTPException(404, "no FUP policy configured for tenant")
    counter = reset_cycle(session, payload.tenant_id, subscriber_id, fup, actor=payload.actor)
    session.commit()
    return {"subscriber_id": str(subscriber_id), "cycle": counter.cycle, "throttled": False}


@router.post("/fup/subscribers/{subscriber_id}/topup")
def fup_topup(subscriber_id: uuid.UUID, payload: FupTopUp, session: Session = Depends(db)):
    fup = session.scalar(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == payload.tenant_id).order_by(FairUsagePolicy.created_at.desc()))
    if fup is None:
        raise HTTPException(404, "no FUP policy configured for tenant")
    counter = apply_topup(session, payload.tenant_id, subscriber_id, fup, payload.topup_bytes, actor=payload.actor)
    session.commit()
    return {"subscriber_id": str(subscriber_id), "cycle": counter.cycle, "topup_bytes": counter.topup_bytes, "throttled": counter.throttled}


@router.post("/fup/subscribers/{subscriber_id}/preview")
def fup_preview(subscriber_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    fup = session.scalar(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == tenant_id).order_by(FairUsagePolicy.created_at.desc()))
    if fup is None:
        raise HTTPException(404, "no FUP policy configured for tenant")
    tier = evaluate_fup(session, tenant_id, subscriber_id, fup)
    return {"subscriber_id": str(subscriber_id), "active_tier": tier, "radius_attributes": compile_radius_attributes(tier or {})}


# ===========================================================================
# Bandwidth / QoS / FUP catalog
# ===========================================================================

@router.post("/bandwidth-profiles", status_code=201)
def create_bandwidth_profile(payload: BandwidthProfileCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    item = BandwidthProfile(**payload.model_dump())
    session.add(item)
    _audit(session, payload.tenant_id, "bandwidth_profile.created", str(item.id), {"code": payload.code})
    session.commit()
    return {"id": str(item.id), "code": item.code}


@router.get("/bandwidth-profiles")
def list_bandwidth_profiles(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "name": item.name, "upload_kbps": item.upload_kbps, "download_kbps": item.download_kbps, "burst_upload_kbps": item.burst_upload_kbps, "burst_download_kbps": item.burst_download_kbps, "priority": item.priority}
        for item in session.scalars(select(BandwidthProfile).where(BandwidthProfile.tenant_id == tenant_id).order_by(BandwidthProfile.code))
    ]


@router.post("/traffic-classes", status_code=201)
def create_traffic_class(payload: TrafficClassCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    item = TrafficClass(**payload.model_dump())
    session.add(item)
    _audit(session, payload.tenant_id, "traffic_class.created", str(item.id), {"code": payload.code})
    session.commit()
    return {"id": str(item.id), "code": item.code}


@router.get("/traffic-classes")
def list_traffic_classes(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "name": item.name, "dscp": item.dscp, "protocol": item.protocol, "priority": item.priority, "packet_mark": item.packet_mark}
        for item in session.scalars(select(TrafficClass).where(TrafficClass.tenant_id == tenant_id).order_by(TrafficClass.code))
    ]


@router.post("/qos-profiles", status_code=201)
def create_qos_profile(payload: QosProfileCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    classes = list(session.scalars(select(TrafficClass).where(TrafficClass.tenant_id == payload.tenant_id, TrafficClass.code.in_(payload.traffic_class_codes))))
    item = QosProfile(tenant_id=payload.tenant_id, code=payload.code, name=payload.name, tier=payload.tier, traffic_class_ids=[str(c.id) for c in classes], params=payload.params)
    session.add(item)
    session.flush()
    _audit(session, payload.tenant_id, "qos_profile.created", str(item.id), {"code": payload.code, "traffic_classes": payload.traffic_class_codes})
    session.commit()
    return {"id": str(item.id), "code": item.code, "traffic_class_ids": item.traffic_class_ids}


@router.get("/qos-profiles")
def list_qos_profiles(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "name": item.name, "tier": item.tier, "traffic_class_ids": item.traffic_class_ids}
        for item in session.scalars(select(QosProfile).where(QosProfile.tenant_id == tenant_id).order_by(QosProfile.code))
    ]


@router.post("/qos-profiles/{qos_id}/compile")
def compile_qos(qos_id: uuid.UUID, tenant_id: uuid.UUID, policy_version_id: uuid.UUID, nas_id: uuid.UUID | None = None, session: Session = Depends(db)):
    profile = _tenant_item(session, QosProfile, qos_id, tenant_id, "QoS profile")
    classes = list(session.scalars(select(TrafficClass).where(TrafficClass.tenant_id == tenant_id, TrafficClass.id.in_([uuid.UUID(v) for v in profile.traffic_class_ids]))))
    version = session.get(NetworkPolicyVersion, policy_version_id)
    if version is None or version.tenant_id != tenant_id:
        raise HTTPException(404, "policy version not found")
    objects = compile_qos_profile(profile, classes, tenant_id, version.policy_id, version.version)
    return {"qos_profile_id": str(qos_id), "objects": objects, "object_count": len(objects)}


@router.post("/fup-policies", status_code=201)
def create_fup_policy(payload: FupPolicyCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    if not payload.thresholds:
        raise HTTPException(422, "at least one threshold is required")
    item = FairUsagePolicy(**payload.model_dump())
    session.add(item)
    _audit(session, payload.tenant_id, "fup_policy.created", str(item.id), {"code": payload.code})
    session.commit()
    return {"id": str(item.id), "code": item.code, "thresholds": len(item.thresholds)}


@router.get("/fup-policies")
def list_fup_policies(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "name": item.name, "cycle": item.cycle, "thresholds": item.thresholds, "reset_rule": item.reset_rule, "grace_bytes": item.grace_bytes}
        for item in session.scalars(select(FairUsagePolicy).where(FairUsagePolicy.tenant_id == tenant_id).order_by(FairUsagePolicy.code))
    ]


# ===========================================================================
# IP identity
# ===========================================================================

@router.get("/ip-identity/search")
def ip_identity_search(tenant_id: uuid.UUID, ip: str | None = None, username: str | None = None, mac: str | None = None, session_id: str | None = None, nas_id: uuid.UUID | None = None, limit: int = 50, session: Session = Depends(db)):
    return search_identity(session, tenant_id, ip=ip, username=username, mac=mac, session_id=session_id, nas_id=nas_id, limit=limit)


@router.get("/ip-identity/{ip_address}/history")
def ip_identity_history(ip_address: str, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return ip_history(session, tenant_id, ip_address)


@router.get("/ip-identity/{ip_address}/regulatory")
def ip_identity_regulatory(ip_address: str, tenant_id: uuid.UUID, actor: str = "operator", session: Session = Depends(db)):
    """Authorized regulatory lookup. Requires the aaa.ip.regulatory_lookup
    permission (enforced by the role RBAC); always audited."""
    _tenant(session, tenant_id)
    rows = regulatory_lookup(session, tenant_id, ip=ip_address, actor=actor)
    session.commit()
    return {"ip": ip_address, "records": rows}
