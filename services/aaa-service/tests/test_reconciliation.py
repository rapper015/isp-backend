from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ActiveSession, Nas, Tenant
from app.reconciliation import reconcile_nas_sessions
from datetime import datetime, timezone

def test_reconciliation_reports_differences_without_commands():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="reconcile"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1"); session.add(nas); session.flush()
    session.add(ActiveSession(tenant_id=tenant.id, nas_id=nas.id, username="alice", session_id="db-1", started_at=datetime.now(timezone.utc), status="ACTIVE")); session.commit()
    assert reconcile_nas_sessions(session, tenant.id, nas.id, {"router-1"}) == {"database_only": ["db-1"], "router_only": ["router-1"], "matching": []}
