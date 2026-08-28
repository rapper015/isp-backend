from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ActiveSession, Nas, RadiusServer, Tenant
from app.workers import detect_stale_sessions, evaluate_radius_server_health

def test_worker_marks_only_expired_active_sessions_stale():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="worker-tenant"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1"); session.add(nas); session.flush()
    stale = ActiveSession(tenant_id=tenant.id, nas_id=nas.id, username="alice", session_id="s1", started_at=datetime.now(timezone.utc), last_interim_at=datetime.now(timezone.utc) - timedelta(hours=1), status="ACTIVE")
    session.add(stale); session.commit()
    assert detect_stale_sessions(session, 60) == 1
    assert stale.status == "STALE"

def test_worker_marks_missing_radius_heartbeat_unhealthy():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    server = RadiusServer(name="radius-test", host="192.0.2.2", api_key_hash="hash", health="unknown"); session.add(server); session.commit()
    assert evaluate_radius_server_health(session, 60) == 1
    assert server.health == "unhealthy"
