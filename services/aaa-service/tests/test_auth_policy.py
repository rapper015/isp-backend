from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ActiveSession, Credential, Nas, Tenant, UsageProjection
from app.services import authenticate

def test_authentication_rejects_bound_mac_mismatch():
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="mac-policy"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1"); session.add(nas); session.flush()
    session.add(Credential(tenant_id=tenant.id, subscriber_id=uuid4(), username="alice", username_normalized="alice", password_hash="$2b$12$NPe90Fk3FayqoDTefWUdmOMSuyrQt14PqSjv2EzAXw2R4aZMLayqK", mac_address="aa:bb:cc:dd:ee:ff")); session.commit()
    decision, _ = authenticate(session, {"User-Name": "alice", "User-Password": "wrong", "NAS-IP-Address": "10.0.0.1", "Calling-Station-Id": "11:22:33:44:55:66"}, "c")
    assert decision == "REJECT_MAC_MISMATCH"
