from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.commands import CommandResult, RadiusCommandAdapter
from app.database import Base
from app.models import Nas, RadiusCommand, Tenant
from app.security import encrypt_secret
from app.workers import process_radius_command

class AcceptingAdapter(RadiusCommandAdapter):
    def send_disconnect(self, *args): return CommandResult("ACKNOWLEDGED")
    def send_coa(self, *args): return CommandResult("ACKNOWLEDGED")
    def test_connectivity(self, *args): return CommandResult("ACKNOWLEDGED")

def test_command_worker_transitions_to_acknowledged(monkeypatch):
    monkeypatch.setenv("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
    engine = create_engine("sqlite://"); Base.metadata.create_all(engine); session = sessionmaker(bind=engine)()
    tenant = Tenant(name="commands"); session.add(tenant); session.flush()
    nas = Nas(tenant_id=tenant.id, name="edge", source_ip="10.0.0.1", secret_ciphertext=encrypt_secret("not-a-real-secret")); session.add(nas); session.flush()
    command = RadiusCommand(tenant_id=tenant.id, nas_id=nas.id, command_type="DISCONNECT", idempotency_key=str(uuid4()), correlation_id="c1", attributes={"Acct-Session-Id": "session-1"}, status="QUEUED"); session.add(command); session.commit()
    assert process_radius_command(session, AcceptingAdapter()) == "ACKNOWLEDGED"
    assert command.status == "ACKNOWLEDGED"
