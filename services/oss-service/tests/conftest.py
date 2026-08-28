"""Hermetic test environment for the OSS service (Milestone 2)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_oss.db")
os.environ.setdefault("OSS_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("OSS_INTERNAL_API_KEYS", "test-internal-key")
os.environ.setdefault("OSS_JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("OSS_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
os.environ.setdefault("OSS_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")

import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.integrations.fakes import reset_adapters  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.services.resource_service import ResourceService  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_oss.db", "oss.db"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except (PermissionError, FileNotFoundError):
                pass
    Base.metadata.create_all(bind=engine)
    yield
    if os.path.exists("test_oss.db"):
        try:
            os.remove("test_oss.db")
        except (PermissionError, FileNotFoundError):
            pass


@pytest.fixture(autouse=True)
def _reset_adapters():
    reset_adapters()
    yield
    reset_adapters()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def tenant_id(session) -> uuid.UUID:
    tenant = Tenant(name="Test ISP", code=f"TEST-{uuid.uuid4().hex[:8].upper()}")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant.id


@pytest.fixture
def seeded_resources(session, tenant_id) -> None:
    service = ResourceService(session)
    service.seed(tenant_id, "IPV4", [f"10.0.{i}.10" for i in range(1, 6)])
    service.seed(tenant_id, "VLAN", [f"vlan-{100 + i}" for i in range(5)])
    service.seed(tenant_id, "PON_PORT", [f"pon-{i}" for i in range(5)])
    session.commit()


def make_token(role: str = "OSS_MANAGER", tenant: uuid.UUID | None = None) -> str:
    claims = {
        "userId": "test-user",
        "role": role,
        "permissions": [],
        "tenant_id": str(tenant) if tenant else None,
    }
    return jwt.encode(claims, os.environ["OSS_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def auth_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('OSS_MANAGER', tenant_id)}"}


def new_connection_payload(tenant_id, customer_id="cust-valid-001", plan="plan-fiber-100", **overrides) -> dict:
    payload = {
        "tenant_id": str(tenant_id),
        "order_type": "NEW_CONNECTION",
        "customer_id": customer_id,
        "service_location_id": "loc-1",
        "requested_plan_reference": plan,
        "priority": "HIGH",
        "source_channel": "CRM",
        "requested_snapshot": {"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test", "pop": "pop-1", "node": "node-1"},
    }
    payload.update(overrides)
    return payload
