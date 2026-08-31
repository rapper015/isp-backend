"""Alert normalization, fingerprint stability, dedup, grouping, inhibition,
silencing, flapping, routing fallback and lifecycle."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.alerts import fingerprint, grouping_key, is_flapping, should_dedupe
from app.models import Alert, AlertEvent, AlertSilence, NotificationDelivery
from app.services import alert_service


def _now():
    return datetime.now(timezone.utc)


def test_fingerprint_stable():
    fp1 = fingerprint("aaa", "cpu_high", "nas-1", "nas", "ten-a")
    fp2 = fingerprint("aaa", "cpu_high", "nas-1", "nas", "ten-a")
    fp3 = fingerprint("aaa", "cpu_high", "nas-1", "nas", "ten-b")
    assert fp1 == fp2
    assert fp1 != fp3
    # No timestamps or randomness
    assert all(part not in fp1 for part in (_now().isoformat(), uuid.uuid4().hex))


def test_grouping_key():
    labels = {"service": "aaa", "component": "radius", "resource": "nas-1"}
    key = grouping_key(labels)
    assert key == "aaa|radius|nas-1|"


def test_should_dedupe():
    assert should_dedupe(True, None, 0, 300) is True
    assert should_dedupe(False, 1000.0, 1100.0, 300) is True
    assert should_dedupe(False, 1000.0, 2000.0, 300) is False


def test_ingest_creates_firing_alert(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="cpu_high", tenant_id=tenant_id,
        severity="HIGH", component="radius", resource="nas-1", labels={"service": "aaa"})
    session.commit()
    assert alert.state == "FIRING"
    assert alert.firing_count == 1
    assert alert.fingerprint.startswith("aaa|cpu_high|nas_1|radius|")


def test_dedup_increments_firing_count(defaults, session, tenant_id):
    a1 = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="cpu_high", tenant_id=tenant_id,
        severity="HIGH", component="radius", resource="nas-1")
    session.commit()
    a2 = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="cpu_high", tenant_id=tenant_id,
        severity="HIGH", component="radius", resource="nas-1")
    session.commit()
    assert a1.id == a2.id
    assert a2.firing_count >= 2


def test_severity_with_impact_elevates():
    from app.domain.alerts import severity_with_impact
    assert severity_with_impact("LOW", customer_impact=True) == "MEDIUM"
    assert severity_with_impact("HIGH", customer_impact=True) == "HIGH"
    assert severity_with_impact("MEDIUM", customer_impact=False) == "MEDIUM"


def test_lifecycle_acknowledge_resolve(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="disk_full", tenant_id=tenant_id, severity="HIGH")
    session.commit()
    alert_service.acknowledge(session, alert.id, "noc-1")
    session.commit()
    assert alert.state == "ACKNOWLEDGED"
    alert_service.resolve(session, alert.id, actor="noc-1")
    session.commit()
    assert alert.state == "RESOLVED"
    assert alert.resolved_at is not None


def test_silence_suppresses_firing(defaults, session, tenant_id):
    silence = AlertSilence(tenant_id=tenant_id, match_labels={"service": "aaa"},
                           starts_at=_now() - timedelta(minutes=1), ends_at=_now() + timedelta(hours=1),
                           reason="planned", state="ACTIVE")
    session.add(silence)
    session.commit()
    alert = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="cpu_high", tenant_id=tenant_id,
        severity="HIGH", labels={"service": "aaa"})
    session.commit()
    silenced = alert_service.apply_silence(session, alert)
    session.commit()
    assert silenced is True
    assert alert.state == "SILENCED"


def test_dependency_inhibition(defaults, session, tenant_id):
    parent = alert_service.normalize_and_ingest(
        session, service="routeros", alert_name="pop_router_down", tenant_id=tenant_id,
        severity="CRITICAL", component="pop", resource="pop-1",
        labels={"service": "routeros", "component": "pop"})
    session.commit()
    child = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="cpe_offline", tenant_id=tenant_id,
        severity="HIGH", component="pop", resource="cpe-1",
        labels={"service": "aaa", "component": "pop"})
    session.commit()
    # child shares component (pop) with a CRITICAL parent that is firing -> suppressed
    suppressed = alert_service.dependency_check(session, child)
    session.commit()
    assert suppressed is True
    assert child.state == "SUPPRESSED"


def test_flapping_detection():
    assert is_flapping(["FIRING", "RESOLVED", "FIRING", "RESOLVED"]) is True
    assert is_flapping(["FIRING", "FIRING", "FIRING"]) is False


def test_routing_with_fallback(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="billing", alert_name="payment_failed", tenant_id=tenant_id,
        severity="CRITICAL", labels={"service": "billing", "severity": "CRITICAL"})
    session.commit()
    deliveries = session.query(NotificationDelivery).filter(NotificationDelivery.alert_id == alert.id).all()
    assert len(deliveries) >= 1
    # DEFAULT route is a fallback when nothing matches; NOC_DASHBOARD matches severity=CRITICAL
    assert any(d.route == "NOC_DASHBOARD" for d in deliveries)


def test_route_fallback_when_no_match(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="workforce", alert_name="job_slow", tenant_id=tenant_id,
        severity="LOW", labels={"service": "workforce"})
    session.commit()
    deliveries = session.query(NotificationDelivery).filter(NotificationDelivery.alert_id == alert.id).all()
    assert len(deliveries) >= 1
    assert any(d.route == "DEFAULT" for d in deliveries)


def test_expire_alert(defaults, session, tenant_id):
    alert = alert_service.normalize_and_ingest(
        session, service="aaa", alert_name="stale", tenant_id=tenant_id, severity="LOW")
    session.commit()
    alert_service.expire(session, alert.id)
    session.commit()
    assert alert.state == "EXPIRED"
