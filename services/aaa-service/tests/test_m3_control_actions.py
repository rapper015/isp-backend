"""M3 control actions: CoA/Disconnect registry with ACK/NAK/TIMEOUT outcomes,
retry, idempotency, and the DISCONNECT_AND_REAUTHORIZE strategy."""
import uuid

import pytest

from app.models import ActiveSession, ControlAction
from app.network_control.control_actions import (
    cancel,
    create_control_action,
    mark_sent,
    record_outcome,
    retry,
    strategy_for_change,
)


def _session(session, tenant, nas) -> ActiveSession:
    item = ActiveSession(
        tenant_id=tenant.id,
        nas_id=nas.id,
        subscriber_id=uuid.uuid4(),
        username="cust-a",
        session_id=f"ses-{uuid.uuid4().hex[:8]}",
        status="ACTIVE",
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        framed_ip="198.51.100.10",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_strategy_unsupported_ip_change():
    assert strategy_for_change({"Framed-IP-Address": "198.51.100.20"}) == "DISCONNECT_AND_REAUTHORIZE"
    assert strategy_for_change({"Mikrotik-Rate-Limit": "8M/4M"}) == "COA"


def test_create_control_action_persists_and_is_idempotent(session, tenant, nas, subscriber):
    active = _session(session, tenant, nas)
    action = create_control_action(
        session,
        tenant.id,
        action_type="COA",
        trigger="operator",
        nas_id=nas.id,
        session_id=active.id,
        subscriber_id=subscriber.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Mikrotik-Rate-Limit": "8M/4M"},
        idempotency_key=f"coa-{uuid.uuid4().hex}",
    )
    session.commit()
    assert action.status == "PENDING"
    assert action.strategy == "COA"
    again = create_control_action(
        session,
        tenant.id,
        action_type="COA",
        trigger="operator",
        nas_id=nas.id,
        session_id=active.id,
        subscriber_id=subscriber.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Mikrotik-Rate-Limit": "8M/4M"},
        idempotency_key=action.idempotency_key,
    )
    session.commit()
    assert again.id == action.id


def test_ack_outcome(session, tenant, nas, subscriber):
    active = _session(session, tenant, nas)
    action = create_control_action(
        session,
        tenant.id,
        action_type="COA",
        trigger="operator",
        nas_id=nas.id,
        session_id=active.id,
        subscriber_id=subscriber.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Mikrotik-Rate-Limit": "8M/4M"},
        idempotency_key=f"ack-{uuid.uuid4().hex}",
    )
    session.commit()
    mark_sent(session, action.id)
    session.commit()
    record_outcome(session, action.id, "ACK", latency_ms=12)
    session.commit()
    action = session.get(ControlAction, action.id)
    assert action.status == "ACK"
    assert action.ack_at is not None
    assert action.latency_ms == 12
    assert action.attempts == 1


def test_nak_and_timeout_outcomes(session, tenant, nas, subscriber):
    active = _session(session, tenant, nas)
    for outcome in ("NAK", "TIMEOUT"):
        action = create_control_action(
            session,
            tenant.id,
            action_type="DISCONNECT",
            trigger="operator",
            nas_id=nas.id,
            session_id=active.id,
            subscriber_id=subscriber.subscriber_id,
            username=active.username,
            session_identifier={"Acct-Session-Id": active.session_id},
            requested_attributes={"Acct-Session-Id": active.session_id},
            idempotency_key=f"{outcome.lower()}-{uuid.uuid4().hex}",
        )
        session.commit()
        record_outcome(session, action.id, outcome, detail={"error": "nas rejected"})
        session.commit()
        action = session.get(ControlAction, action.id)
        assert action.status == outcome
        assert action.response != {}


def test_retry_and_cancel(session, tenant, nas, subscriber):
    active = _session(session, tenant, nas)
    action = create_control_action(
        session,
        tenant.id,
        action_type="COA",
        trigger="operator",
        nas_id=nas.id,
        session_id=active.id,
        subscriber_id=subscriber.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Mikrotik-Rate-Limit": "8M/4M"},
        idempotency_key=f"rc-{uuid.uuid4().hex}",
    )
    session.commit()
    record_outcome(session, action.id, "TIMEOUT")
    session.commit()
    action = session.get(ControlAction, action.id)
    assert action.status == "TIMEOUT"
    retry(session, action.id)
    session.commit()
    assert session.get(ControlAction, action.id).status == "PENDING"
    cancel(session, action.id)
    session.commit()
    assert session.get(ControlAction, action.id).status == "CANCELLED"


def test_disconnect_and_reauth_strategy_recorded(session, tenant, nas, subscriber):
    active = _session(session, tenant, nas)
    action = create_control_action(
        session,
        tenant.id,
        action_type="DISCONNECT",
        trigger="disconnect_and_reauth",
        nas_id=nas.id,
        session_id=active.id,
        subscriber_id=subscriber.subscriber_id,
        username=active.username,
        session_identifier={"Acct-Session-Id": active.session_id},
        requested_attributes={"Acct-Session-Id": active.session_id, "Framed-IP-Address": "198.51.100.20"},
        idempotency_key=f"reauth-{uuid.uuid4().hex}",
    )
    session.commit()
    assert action.strategy == "DISCONNECT_AND_REAUTHORIZE"
