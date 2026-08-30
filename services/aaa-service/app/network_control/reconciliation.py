"""Reconciliation: classify session/device mismatches safely.

Mismatches are never auto-disconnected without an explicit decision. Each
mismatch is classified (informational / repairable / policy-reapply /
disconnect / manual / security-critical) and safe suggestions are produced."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActiveSession
from .enums import MISMATCH_CLASSIFICATIONS


def classify_mismatch(kind: str, detail: dict | None = None) -> dict:
    """Deterministic classification of a reconciliation mismatch."""
    classification = MISMATCH_CLASSIFICATIONS[0]
    action = "observe"
    reason = "informational difference"
    if kind == "router_only":
        classification = "REPAIRABLE"
        action = "mark unknown session for review"
        reason = "session present on router but missing in AAA"
    elif kind == "database_only":
        classification = "REQUIRES_DISCONNECT" if (detail or {}).get("suspended") else "INFORMATIONAL"
        action = "review before disconnect" if classification == "REQUIRES_DISCONNECT" else "observe"
        reason = "session active in AAA but missing on router"
    elif kind == "suspended_subscriber_online":
        classification = "SECURITY_CRITICAL"
        action = "disconnect (requires approval)"
        reason = "suspended subscriber is still online"
    elif kind == "wrong_rate":
        classification = "REQUIRES_POLICY_REAPPLY"
        action = "reapply policy via CoA"
        reason = "applied rate differs from desired policy"
    elif kind == "unknown_dynamic_queue":
        classification = "REPAIRABLE"
        action = "mark and preserve manual queue"
        reason = "unowned dynamic queue detected"
    elif kind == "wrong_ip":
        classification = "REQUIRES_DISCONNECT"
        action = "disconnect and re-authorize"
        reason = "assigned IP differs from IPAM ownership"
    elif kind == "duplicate_session":
        classification = "REQUIRES_MANUAL_INTERVENTION"
        action = "manual intervention required"
        reason = "duplicate session detected"
    return {"classification": classification, "action": action, "reason": reason}


def classify_nas_sessions(session: Session, tenant_id, nas_id, router_session_ids: set[str], *, suspended_subscriber_ids: set | None = None) -> dict:
    """Combine router vs AAA session diff with classification and safe
    suggestions. Simulation only — never applies changes."""
    database = list(
        session.scalars(
            select(ActiveSession).where(
                ActiveSession.tenant_id == tenant_id,
                ActiveSession.nas_id == nas_id,
                ActiveSession.status.in_(["STARTING", "ACTIVE", "STALE", "ORPHANED"]),
            )
        )
    )
    database_ids = {item.session_id for item in database}
    suspended_subscriber_ids = suspended_subscriber_ids or set()

    def _entry(kind: str, session_id: str, detail: dict | None = None) -> dict:
        return {"session_id": session_id, **classify_mismatch(kind, detail)}

    database_only = [_entry("database_only", sid, {"suspended": sid in suspended_subscriber_ids}) for sid in sorted(database_ids - router_session_ids)]
    router_only = [_entry("router_only", sid) for sid in sorted(router_session_ids - database_ids)]
    suspended_online = [_entry("suspended_subscriber_online", item.session_id) for item in database if item.subscriber_id in suspended_subscriber_ids and item.session_id in router_session_ids]
    return {
        "database_only": database_only,
        "router_only": router_only,
        "suspended_subscriber_online": suspended_online,
        "matching": sorted(database_ids & router_session_ids),
        "applied": False,
        "note": "simulation only; no session was disconnected",
    }
