"""Integration: consumers, idempotency, envelope trace-context propagation."""
import uuid

from app.events import canonical_event_type, consume_once, envelope, outbox, unprocessed_events
from app.messaging import consumers
from app.models import ChangeEvent, InboxMessage, NetworkObservation, SlIMeasurement


def test_envelope_requires_known_type():
    from app.domain.exceptions import AssuranceError
    try:
        envelope("unknown.event.v1", None, {})
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_envelope_includes_trace_context_slot():
    ev = envelope("assurance.alert_normalized.v1", uuid.uuid4(),
                  {"service": "aaa"},
                  trace_context={"traceparent": "00-abc"})
    assert ev["trace_context"] == {"traceparent": "00-abc"}
    assert ev["event_type"] == "assurance.alert_normalized.v1"
    assert ev["tenant_id"]


def test_consume_once_is_idempotent(session):
    event_id = str(uuid.uuid4())
    assert consume_once(session, event_id, "assurance:test") is True
    session.flush()
    assert consume_once(session, event_id, "assurance:test") is False
    rows = session.query(InboxMessage).filter(InboxMessage.event_id == event_id).all()
    assert len(rows) == 1


def test_canonical_event_type_resolves_alias():
    assert canonical_event_type("payment.captured.v1") == "billing.payment.captured.v1"
    assert canonical_event_type("billing.payment.captured.v1") == "billing.payment.captured.v1"


def test_consumer_records_sli_and_kpi(defaults, session, tenant_id):
    ev = envelope("billing.payment.captured.v1", tenant_id, {"amount": 100},
                  trace_context={"traceparent": "00-1"})
    consumers.handle(session, ev)
    session.commit()
    from app.models import KpiMeasurement
    sli_rows = session.query(SlIMeasurement).all()
    kpi_rows = session.query(KpiMeasurement).all()
    assert len(sli_rows) == 1  # SLI measurement
    assert len(kpi_rows) == 1  # KPI measurement


def test_consumer_ignores_duplicate_event(defaults, session, tenant_id):
    ev = envelope("oss.order.created.v1", tenant_id, {})
    consumers.handle(session, ev)
    session.commit()
    count_before = session.query(SlIMeasurement).count()
    consumers.handle(session, ev)  # same event_id
    session.commit()
    assert session.query(SlIMeasurement).count() == count_before


def test_consumer_ignores_unconsumed_event(defaults, session, tenant_id):
    ev = {"event_id": str(uuid.uuid4()), "event_type": "unknown.type.v1",
          "tenant_id": str(tenant_id), "payload": {}}
    consumers.handle(session, ev)
    session.commit()
    assert session.query(SlIMeasurement).count() == 0


def test_consumer_records_change_event(defaults, session, tenant_id):
    ev = envelope("network.policy.deployed.v1", tenant_id, {"deployment_id": "dep-1"})
    consumers.handle(session, ev)
    session.commit()
    changes = session.query(ChangeEvent).all()
    assert len(changes) == 1
    assert changes[0].change_type == "DEPLOYMENT"
    assert changes[0].entity_ref == "dep-1"


def test_consumer_records_network_observation(defaults, session, tenant_id):
    ev = envelope("nas.health_changed.v1", tenant_id, {"nas_id": "nas-9"})
    consumers.handle(session, ev)
    session.commit()
    obs = session.query(NetworkObservation).all()
    assert len(obs) == 1
    assert obs[0].device_ref == "nas-9"


def test_outbox_and_unprocessed(session):
    outbox(session, "assurance.slo_breached.v1", uuid.uuid4(), "corr-1", {"code": "slo-1"})
    session.commit()
    pending = unprocessed_events(session)
    assert len(pending) == 1
    assert pending[0].event_type == "assurance.slo_breached.v1"
