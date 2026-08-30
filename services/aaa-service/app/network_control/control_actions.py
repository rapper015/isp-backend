"""CoA / Disconnect control-action registry.

Every control action persists its outcome (ACK / NAK / TIMEOUT), latency and
retry policy. Requests are idempotent per (tenant, idempotency_key). Unsupported
live changes (e.g. IP/pool changes) use the controlled DISCONNECT_AND_REAUTHORIZE
strategy instead of a failed CoA."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ControlAction, RadiusCommand
from ..services import audit, correlation, outbox
from .enums import CONTROL_ACTION_STATUS, CONTROL_ACTION_TYPES

# Attributes that cannot be changed live via CoA on MikroTik and require
# disconnect + re-authentication.
COA_UNSUPPORTED_KEYS = {"Framed-IP-Address", "Framed-IPv6-Prefix", "Framed-Pool", "Framed-IPv6-Pool", "Framed-Route"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_action_type(action_type: str) -> str:
    if action_type not in CONTROL_ACTION_TYPES:
        raise ValueError(f"invalid control action type {action_type!r}")
    return action_type


def strategy_for_change(requested_attributes: dict) -> str:
    """Choose COA vs DISCONNECT_AND_REAUTHORIZE for a live attribute change."""
    if any(key in requested_attributes for key in COA_UNSUPPORTED_KEYS):
        return "DISCONNECT_AND_REAUTHORIZE"
    return "COA"


def create_control_action(
    session: Session,
    tenant_id,
    *,
    action_type: str,
    trigger: str,
    nas_id,
    session_id=None,
    subscriber_id=None,
    username=None,
    session_identifier: dict | None = None,
    requested_attributes: dict | None = None,
    idempotency_key: str,
    actor: str = "system",
    correlation_id: str | None = None,
) -> ControlAction:
    _validate_action_type(action_type)
    request_id = correlation(correlation_id)
    existing = session.scalar(select(ControlAction).where(ControlAction.tenant_id == tenant_id, ControlAction.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    attributes = requested_attributes or {}
    strategy = strategy_for_change(attributes)
    action = ControlAction(
        tenant_id=tenant_id,
        action_type=action_type,
        trigger=trigger,
        nas_id=nas_id,
        session_id=session_id,
        subscriber_id=subscriber_id,
        username=username,
        session_identifier=session_identifier or {},
        requested_attributes=attributes,
        status="PENDING",
        strategy=strategy,
        idempotency_key=idempotency_key,
        correlation_id=request_id,
        actor=actor,
    )
    session.add(action)
    session.flush()
    # Companion queue command for the existing AAA worker path.
    session.add(
        RadiusCommand(
            tenant_id=tenant_id,
            nas_id=nas_id,
            session_id=session_id,
            subscriber_id=subscriber_id,
            command_type="DISCONNECT" if action_type == "DISCONNECT" else "COA",
            status="QUEUED",
            idempotency_key=f"nc:{idempotency_key}",
            correlation_id=request_id,
            attributes=attributes,
        )
    )
    event_type = "aaa.disconnect.requested.v1" if action_type == "DISCONNECT" else "aaa.coa.requested.v1"
    outbox(session, event_type, tenant_id, request_id, {"control_action_id": str(action.id), "action_type": action_type, "session_id": str(session_id) if session_id else None, "subscriber_id": str(subscriber_id) if subscriber_id else None, "strategy": strategy}, idempotency_key)
    audit(session, tenant_id, f"control_action.{action_type.lower()}.requested", str(action.id), request_id, {"trigger": trigger, "strategy": strategy, "attributes": list(attributes)})
    return action


def mark_sent(session: Session, action_id) -> ControlAction:
    action = session.get(ControlAction, action_id)
    if action is None:
        raise ValueError("control action not found")
    if action.status == "PENDING":
        action.status = "SENT"
        action.sent_at = _now()
        action.attempts += 1
    return action


def record_outcome(session: Session, action_id, outcome: str, detail: dict | None = None, latency_ms: int | None = None) -> ControlAction:
    """Persist ACK/NAK/TIMEOUT and the audit trail. Idempotent per action."""
    action = session.get(ControlAction, action_id)
    if action is None:
        raise ValueError("control action not found")
    if action.status in ("ACK", "NAK", "TIMEOUT", "FAILED", "SUCCEEDED", "CANCELLED"):
        return action
    outcome = outcome.upper()
    if outcome not in ("ACK", "NAK", "TIMEOUT"):
        raise ValueError(f"invalid control outcome {outcome!r}")
    request_id = action.correlation_id
    if outcome == "ACK":
        action.status = "ACK"
        action.ack_at = _now()
        action.latency_ms = latency_ms
        action.completed_at = _now()
        outbox(session, "aaa.coa.acknowledged.v1" if action.action_type == "COA" else "aaa.disconnect.acknowledged.v1", action.tenant_id, request_id, {"control_action_id": str(action.id), "latency_ms": latency_ms}, action.idempotency_key)
    elif outcome == "NAK":
        action.status = "NAK"
        action.nak_at = _now()
        action.latency_ms = latency_ms
        action.response = detail or {}
        action.completed_at = _now()
        outbox(session, "aaa.coa.rejected.v1" if action.action_type == "COA" else "aaa.disconnect.rejected.v1", action.tenant_id, request_id, {"control_action_id": str(action.id), "detail": detail}, action.idempotency_key)
    else:  # TIMEOUT
        action.status = "TIMEOUT"
        action.timeout_at = _now()
        action.latency_ms = latency_ms
        action.response = detail or {}
        outbox(session, "aaa.coa.timed_out.v1", action.tenant_id, request_id, {"control_action_id": str(action.id)}, action.idempotency_key)
    audit(session, action.tenant_id, f"control_action.{action.action_type.lower()}.{outcome.lower()}", str(action.id), request_id, {"detail": detail, "latency_ms": latency_ms})
    return action


def retry(session: Session, action_id, actor: str = "operator") -> ControlAction:
    action = session.get(ControlAction, action_id)
    if action is None:
        raise ValueError("control action not found")
    if action.attempts >= action.max_attempts:
        raise ValueError("control action exhausted its retry limit")
    action.status = "PENDING"
    action.sent_at = None
    action.response = {}
    return action


def cancel(session: Session, action_id, actor: str = "operator") -> ControlAction:
    action = session.get(ControlAction, action_id)
    if action is None:
        raise ValueError("control action not found")
    if action.status not in ("PENDING", "SENT", "RETRYING"):
        raise ValueError(f"cannot cancel control action in state {action.status}")
    action.status = "CANCELLED"
    action.completed_at = _now()
    return action
