"""Multi-tenant isolation: fail-closed tenant context, platform aggregates."""
import uuid

import pytest
from fastapi import HTTPException

from app.context import TenantContext, reset_context, set_context
from app.domain.exceptions import TenantContextRequiredError, TenantIsolationError
from app.models import Alert, Incident
from app.routing import enforce_scope, require_platform_aggregate
from app.services import alert_service, incident_service


def _ctx(tenant_id):
    return TenantContext(tenant_id=tenant_id, user_id="u", role="TENANT_ADMIN",
                         permissions=frozenset({"alerts.view"}))


def test_enforce_scope_fails_closed_without_context():
    with pytest.raises(TenantContextRequiredError):
        enforce_scope(uuid.uuid4())


def test_enforce_scope_mismatch_rejected():
    token = set_context(_ctx(uuid.uuid4()))
    try:
        with pytest.raises(TenantIsolationError):
            enforce_scope(uuid.uuid4())
    finally:
        reset_context(token)


def test_enforce_scope_ok_with_matching_context():
    tid = uuid.uuid4()
    token = set_context(_ctx(tid))
    try:
        enforce_scope(tid)
    finally:
        reset_context(token)


def test_platform_aggregate_requires_explicit_scope():
    tid = uuid.uuid4()
    token = set_context(_ctx(tid))  # TENANT scope
    try:
        with pytest.raises(Exception):
            require_platform_aggregate()
    finally:
        reset_context(token)
    # PLATFORM scope works
    token2 = set_context(TenantContext(tenant_id=None, user_id="u", role="PLATFORM_ADMIN",
                                       scope_kind="PLATFORM_AGGREGATE"))
    try:
        require_platform_aggregate()
    finally:
        reset_context(token2)


def test_alert_tenant_isolation(defaults, session):
    a = uuid.uuid4()
    b = uuid.uuid4()
    alert_service.normalize_and_ingest(session, service="aaa", alert_name="cpu", tenant_id=a)
    alert_service.normalize_and_ingest(session, service="aaa", alert_name="cpu", tenant_id=b)
    session.commit()
    a_alerts = alert_service.list_alerts(session, a)
    b_alerts = alert_service.list_alerts(session, b)
    assert len(a_alerts) == 1 and len(b_alerts) == 1
    assert a_alerts[0].id != b_alerts[0].id


def test_incident_tenant_isolation(defaults, session):
    a = uuid.uuid4()
    b = uuid.uuid4()
    incident_service.create_incident(session, tenant_id=a, title="Incident A")
    incident_service.create_incident(session, tenant_id=b, title="Incident B")
    session.commit()
    ids_a = {str(i.id) for i in session.query(Incident).filter(Incident.tenant_id == a).all()}
    ids_b = {str(i.id) for i in session.query(Incident).filter(Incident.tenant_id == b).all()}
    assert ids_a.isdisjoint(ids_b)


def test_platform_aggregate_lists_cross_tenant(defaults, session):
    a = uuid.uuid4()
    b = uuid.uuid4()
    alert_service.normalize_and_ingest(session, service="aaa", alert_name="cpu", tenant_id=a)
    alert_service.normalize_and_ingest(session, service="aaa", alert_name="cpu", tenant_id=b)
    session.commit()
    from app.services import report_service
    agg = report_service.platform_aggregate(session, hours=24)
    assert agg["tenants_with_alerts"] >= 2
    assert agg["is_platform_aggregate"] is True


def test_mgmt_auth_requires_token(client):
    resp = client.get("/api/assurance/v1/alerts")
    assert resp.status_code == 401


def test_mgmt_auth_forbids_low_role(client, tenant_id):
    from conftest import make_token
    headers = {"Authorization": f"Bearer {make_token('READ_ONLY', tenant_id)}"}
    resp = client.post("/api/assurance/v1/slos/00000000-0000-0000-0000-000000000000/approve", headers=headers)
    assert resp.status_code in (403, 404)


def test_platform_endpoint_requires_platform_scope(client, tenant_id, tenant_headers):
    resp = client.get("/api/assurance/v1/dashboards/platform", headers=tenant_headers)
    # TENANT_ADMIN cannot access platform aggregate
    assert resp.status_code in (403, 422)


def test_platform_endpoint_allowed_for_platform(client, platform_headers):
    resp = client.get("/api/assurance/v1/dashboards/platform", headers=platform_headers)
    assert resp.status_code == 200
    assert resp.json()["is_platform_aggregate"] is True
