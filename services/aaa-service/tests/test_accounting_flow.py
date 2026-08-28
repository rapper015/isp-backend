from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ActiveSession, Credential, Nas, Tenant, UsageProjection
from app.services import accounting

def test_start_interim_duplicate_and_stop_project_one_monotonic_session():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="acct"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1"); session.add(nas); session.flush()
    subscriber_id = uuid4()
    session.add(Credential(tenant_id=tenant.id, subscriber_id=subscriber_id, username="alice", username_normalized="alice")); session.commit()
    start = {"User-Name": "alice", "NAS-IP-Address": "10.0.0.1", "Acct-Session-Id": "session-1", "Acct-Status-Type": "start", "Acct-Input-Octets": 5, "Acct-Output-Octets": 10}
    assert accounting(session, start, {}, "c1", "start") == ("OK", True); session.commit()
    interim = {**start, "Acct-Status-Type": "interim-update", "Acct-Input-Octets": 15, "Acct-Output-Octets": 30}
    assert accounting(session, interim, {}, "c2", "interim") == ("OK", True); session.commit()
    assert accounting(session, interim, {}, "c3", "interim") == ("DUPLICATE", True); session.commit()
    stop = {**interim, "Acct-Status-Type": "stop", "Acct-Input-Octets": 20, "Acct-Output-Octets": 40}
    assert accounting(session, stop, {}, "c4", "stop") == ("OK", True); session.commit()
    active = session.scalar(select(ActiveSession)); usage = session.scalar(select(UsageProjection))
    assert active.status == "STOPPED"
    assert (active.input_octets, active.output_octets) == (20, 40)
    assert (usage.input_octets, usage.output_octets) == (20, 40)

def test_accounting_on_marks_existing_nas_sessions_stale_without_losing_history():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="nas-reboot"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.9"); session.add(nas); session.flush()
    active = ActiveSession(tenant_id=tenant.id, nas_id=nas.id, username="alice", session_id="before-reboot", started_at=datetime.now(timezone.utc), status="ACTIVE")
    session.add(active); session.commit()
    result = accounting(session, {"NAS-IP-Address": "10.0.0.9", "Acct-Status-Type": "accounting-on"}, {}, "reboot", "reboot")
    session.commit()
    assert result == ("OK", True)
    assert active.status == "STALE"
