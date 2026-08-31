"""KPI definition, measurement and targeting."""
from app.services import kpi_service


def test_create_kpi(defaults, session):
    kpi = kpi_service.create_kpi(session, {
        "code": "kpi_test_auth", "name": "Auth attempts", "formula": "count(auth.request)",
        "unit": "number", "owner": "NETWORK"})
    session.commit()
    assert kpi.code == "kpi_test_auth"


def test_record_and_latest_measurement(defaults, session, tenant_id):
    kpi_service.create_kpi(session, {
        "code": "kpi_test_login_attempts", "name": "Logins", "formula": "count(login.*)"})
    session.commit()
    kpi_service.record_measurement(session, tenant_id, "kpi_test_login_attempts",
                                   period_key="2024-06-01", value=42, dimensions={"source": "test"})
    session.commit()
    latest = kpi_service.latest_measurement(session, tenant_id, "kpi_test_login_attempts")
    assert latest is not None
    assert latest["value"] == 42


def test_set_target(defaults, session, tenant_id):
    kpi_service.create_kpi(session, {
        "code": "kpi_test_open_tickets", "name": "Open tickets", "formula": "count(ticket.open)"})
    session.commit()
    target = kpi_service.set_target(session, tenant_id, "kpi_test_open_tickets", target=10, direction="BELOW")
    session.commit()
    assert target.direction == "BELOW"
    assert target.target == 10


def test_list_kpis_includes_latest(defaults, session, tenant_id):
    kpi_service.create_kpi(session, {"code": "kpi_x", "name": "X", "formula": "count(x)"})
    session.commit()
    kpi_service.record_measurement(session, tenant_id, "kpi_x", period_key="2024-06-01", value=5)
    session.commit()
    out = kpi_service.list_kpis(session, tenant_id)
    entry = next((e for e in out if e["code"] == "kpi_x"), None)
    assert entry is not None
    assert entry["latest"]["value"] == 5
