from uuid import uuid4
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Credential, Nas, Tenant, UsageProjection
from app.services import accounting

def test_fup_activation_is_idempotent_on_usage_threshold():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="fup", policy={"default_policy": {"fup_threshold_bytes": 10}}); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1"); session.add(nas); session.flush()
    session.add(Credential(tenant_id=tenant.id, subscriber_id=uuid4(), username="alice", username_normalized="alice")); session.commit()
    event = {"User-Name": "alice", "NAS-IP-Address": "10.0.0.1", "Acct-Session-Id": "session-1", "Acct-Status-Type": "start", "Acct-Input-Octets": 10, "Acct-Output-Octets": 0}
    assert accounting(session, event, {}, "fup-1", "fup-1") == ("OK", True); session.commit()
    usage = session.scalar(select(UsageProjection))
    assert usage.fup_active is True
