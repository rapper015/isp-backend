"""End-to-end: alert -> dedup -> incident -> impact -> recovery -> SLO burn -> postmortem."""
import datetime

from conftest import make_token
from app.services import alert_service, incident_service, slo_service


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def test_full_assurance_flow(defaults, session, tenant_id):
    # 1) Ingest an alert -> FIRING, routed
    alert = alert_service.normalize_and_ingest(
        session, service="radius", alert_name="auth_failures", tenant_id=tenant_id,
        severity="CRITICAL", component="radius", resource="nas-1", labels={"service": "radius"})
    session.commit()
    assert alert.state == "FIRING"

    # 2) Dedup: same alert again coalesces
    alert2 = alert_service.normalize_and_ingest(
        session, service="radius", alert_name="auth_failures", tenant_id=tenant_id,
        severity="CRITICAL", component="radius", resource="nas-1", labels={"service": "radius"})
    session.commit()
    assert alert2.id == alert.id
    assert alert2.firing_count >= 2

    # 3) Incident auto-created from alert
    incident = incident_service.create_from_alert(session, alert, is_major=True)
    session.commit()
    assert incident.state == "DETECTED"
    assert incident.is_major is True

    # 4) Impact estimate then confirm
    incident_service.estimate_customer_impact(session, incident.id, impact_kind="INTERNET",
                                              estimated_subscribers=400)
    session.commit()
    incident_service.confirm_customer_impact(session, incident.id, impact_kind="INTERNET",
                                             confirmed_subscribers=312)
    session.commit()

    # 5) Link support ticket (never conflated with incident)
    incident_service.link_ticket(session, incident.id, "SUP-900", relationship="RELATED")
    session.commit()

    # 6) SLO burn: record poor measurements and compute a fast-burn window
    sli = slo_service.create_sli(session, tenant_id, {
        "code": "sli_e2e", "name": "E2E SLI", "good_event_definition": "ok",
        "valid_event_definition": "all"})
    slo = slo_service.create_slo(session, tenant_id, {
        "code": "slo_e2e", "name": "E2E SLO", "sli_id": sli.id, "objective": 0.99,
        "window_seconds": 30 * 24 * 3600, "published": True})
    session.commit()
    slo_service.record_measurement(session, tenant_id, "sli_e2e", good=1, total=100)
    session.commit()
    from app.domain.slos import window_bounds
    start, end = window_bounds(_now(), window_type="ROLLING", window_seconds=30 * 24 * 3600)
    state = slo_service.compute_window(session, tenant_id, slo.id, window_start=start, window_end=end)
    session.commit()
    assert state.fast_burn is True
    assert state.status in ("AT_RISK", "BREACHED", "EXHAUSTED")

    # 7) Investigate: root-cause with evidence, then recovery
    h = incident_service.create_hypothesis(session, incident.id, hypothesis="Radius secret rotated",
                                           created_by="sre-1", is_ai_suggestion=True)
    session.commit()
    incident_service.add_evidence(session, h.id, evidence_type="CHANGE_EVENT",
                                  evidence_ref="secret-rotation-1", supports=True)
    incident_service.transition_hypothesis(session, h.id, "HYPOTHESIS")
    session.commit()
    incident_service.confirm_root_cause(session, h.id, confirmed_by="sre-1")
    session.commit()
    assert h.state == "CONFIRMED_ROOT_CAUSE"

    # 8) Alert resolves, incident walks to RESOLVED
    alert_service.resolve(session, alert.id, actor="sre-1")
    session.commit()
    for target in ("TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "RESOLVED"):
        incident_service.transition(session, incident.id, target, actor="sre-1")
        session.commit()
    assert incident.state == "RESOLVED"

    # 9) Postmortem + action item
    incident_service.require_postmortem(session, incident.id, actor="sre-1")
    session.commit()
    pm = incident_service.create_postmortem(session, incident.id, tenant_id=tenant_id,
                                            summary="Radius secret rotation outage",
                                            root_cause="Secret rotated without grace period")
    session.commit()
    item = incident_service.add_postmortem_action(session, pm.id, title="Add rotation grace period",
                                                  owner="net-eng")
    session.commit()
    assert pm.state == "DRAFT"
    assert item.state == "OPEN"

    # 10) Impact summary reflects confirmed data
    summary = incident_service.impact_summary(session, incident.id)
    assert summary.confirmed_subscribers == 312
    assert summary.estimated is False
