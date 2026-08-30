"""Predictive maintenance + capacity forecasting."""
from datetime import datetime, timedelta, timezone

from app.models import CapacityForecast, FailurePrediction
from app.services import maintenance_service


def _seed_nas(session, tenant_id, nas, error_rate=0.1, latency=300, status="DEGRADED"):
    from app.models import AnalyticalRecord
    now = datetime.now(timezone.utc)
    session.add(AnalyticalRecord(tenant_id=tenant_id, contract="nas.health_changed.v1",
                                 entity_type="nas", entity_ref=nas,
                                 normalized={"nas_id": nas, "error_rate": error_rate,
                                             "latency_avg_ms": latency, "status": status},
                                 event_time=now - timedelta(minutes=5), source="test"))
    session.commit()


def test_failure_prediction(defaults, session, tenant_id):
    _seed_nas(session, tenant_id, "nas-1", error_rate=0.8, latency=900)
    pred = maintenance_service.predict_failure(session, tenant_id=tenant_id, asset_type="nas",
                                               asset_ref="nas-1", horizon_days=14)
    session.commit()
    assert 0.0 <= pred.failure_probability <= 1.0
    assert pred.recommendation_type in ("INSPECT", "DIAGNOSE", "REPLACE", "CONFIG_VALIDATE", "MONITOR")
    assert pred.state == "ACTIVE"


def test_low_risk_prediction_is_monitor(defaults, session, tenant_id):
    _seed_nas(session, tenant_id, "nas-ok", error_rate=0.01, latency=20, status="OK")
    pred = maintenance_service.predict_failure(session, tenant_id=tenant_id, asset_type="nas",
                                               asset_ref="nas-ok", horizon_days=14)
    session.commit()
    assert pred.recommendation_type == "MONITOR"


def test_capacity_forecast_with_trend(defaults, session, tenant_id):
    series = [0.5, 0.52, 0.55, 0.58, 0.62, 0.66, 0.7, 0.75, 0.8, 0.85]
    forecast = maintenance_service.forecast_capacity(
        session, tenant_id=tenant_id, resource_type="ip_pool", resource_ref="pool-1",
        utilization_series=series, horizon_days=30)
    session.commit()
    assert len(forecast.forecast["points"]) == 30
    assert "confidence_interval" in forecast.__dict__
    assert forecast.risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_capacity_risk_alerts_high(defaults, session, tenant_id):
    series = [0.85, 0.87, 0.89, 0.91, 0.93]
    forecast = maintenance_service.forecast_capacity(
        session, tenant_id=tenant_id, resource_type="pop", resource_ref="POP-1",
        utilization_series=series, horizon_days=10)
    session.commit()
    assert forecast.risk in ("HIGH", "CRITICAL")


def test_failure_prediction_records_evidence(defaults, session, tenant_id):
    _seed_nas(session, tenant_id, "nas-2", error_rate=0.6, latency=500)
    pred = maintenance_service.predict_failure(session, tenant_id=tenant_id, asset_type="nas",
                                               asset_ref="nas-2")
    session.commit()
    assert len(pred.evidence) > 0
    assert pred.model_code == "maintenance_baseline"
