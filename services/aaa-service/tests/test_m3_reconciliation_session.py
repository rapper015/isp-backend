"""M3 reconciliation classification and session registry helpers."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import ActiveSession
from app.network_control.reconciliation import classify_mismatch, classify_nas_sessions
from app.network_control.session_registry import classify_stale, detect_orphans, record_timeline, timeline


def _session(session, tenant, nas, status="ACTIVE", interim_minutes_ago=0, session_id=None):
    item = ActiveSession(
        tenant_id=tenant.id,
        nas_id=nas.id,
        subscriber_id=uuid.uuid4(),
        username="cust-a",
        session_id=session_id or f"ses-{uuid.uuid4().hex[:8]}",
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        last_interim_at=datetime.now(timezone.utc) - timedelta(minutes=interim_minutes_ago),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_classify_mismatch_deterministic():
    assert classify_mismatch("router_only")["classification"] == "REPAIRABLE"
    assert classify_mismatch("suspended_subscriber_online")["classification"] == "SECURITY_CRITICAL"
    assert classify_mismatch("wrong_rate")["classification"] == "REQUIRES_POLICY_REAPPLY"
    assert classify_mismatch("wrong_ip")["classification"] == "REQUIRES_DISCONNECT"
    assert classify_mismatch("duplicate_session")["classification"] == "REQUIRES_MANUAL_INTERVENTION"
    assert classify_mismatch("database_only")["classification"] == "INFORMATIONAL"
    assert classify_mismatch("database_only", {"suspended": True})["classification"] == "REQUIRES_DISCONNECT"


def test_classify_nas_sessions_is_simulation_only(session, tenant, nas):
    local = _session(session, tenant, nas, session_id="local-1")
    router_only = {"router-1", "router-2"}
    result = classify_nas_sessions(session, tenant.id, nas.id, router_only)
    assert result["applied"] is False
    assert any(entry["session_id"] == local.session_id for entry in result["database_only"])
    assert len(result["router_only"]) == 2
    assert all(entry["classification"] == "REPAIRABLE" for entry in result["router_only"])


def test_classify_suspended_subscriber_online(session, tenant, nas):
    item = _session(session, tenant, nas, session_id="susp-1")
    result = classify_nas_sessions(session, tenant.id, nas.id, {"susp-1"}, suspended_subscriber_ids={item.subscriber_id})
    assert result["suspended_subscriber_online"][0]["classification"] == "SECURITY_CRITICAL"


def test_timeline_append_and_read(session, tenant, nas):
    item = _session(session, tenant, nas)
    record_timeline(session, tenant.id, item, "session.started", {"ip": "198.51.100.5"}, "corr-1")
    session.commit()
    rows = timeline(session, tenant.id, item.id)
    assert [row.event_type for row in rows] == ["session.started"]


def test_classify_stale_marks_missing_interim(session, tenant, nas):
    _session(session, tenant, nas, interim_minutes_ago=120)
    stale = classify_stale(session, tenant.id, interim_threshold_seconds=600)
    assert len(stale) == 1
    assert stale[0].status == "STALE"


def test_stale_does_not_stop_on_single_delayed_interim(session, tenant, nas):
    _session(session, tenant, nas, interim_minutes_ago=5)  # within window
    stale = classify_stale(session, tenant.id, interim_threshold_seconds=600)
    assert stale == []


def test_detect_orphans(session, tenant, nas):
    _session(session, tenant, nas, status="STALE", interim_minutes_ago=7200)
    orphaned = detect_orphans(session, tenant.id, orphan_after_seconds=3600)
    assert len(orphaned) == 1
    assert orphaned[0].status == "ORPHANED"
