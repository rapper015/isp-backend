"""Hermetic test environment for the support service (Milestone 5)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_support.db")
os.environ.setdefault("SUPPORT_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("SUPPORT_INTERNAL_API_KEYS", "test-internal-key")
os.environ.setdefault("SUPPORT_JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("SUPPORT_CUSTOMER_JWT_SECRET", "test-customer-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("SUPPORT_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
os.environ.setdefault("SUPPORT_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("SUPPORT_ATTACHMENT_DIR", "./test_attachments")

import shutil  # noqa: E402
import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.integrations.fakes import reset_state  # noqa: E402
from app.integrations.base import reset_adapters  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.services import catalog_service  # noqa: E402
from app.services import ticket_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_support.db", "support.db"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except (PermissionError, FileNotFoundError):
                pass
    if os.path.exists("test_attachments"):
        shutil.rmtree("test_attachments", ignore_errors=True)
    Base.metadata.create_all(bind=engine)
    yield
    if os.path.exists("test_support.db"):
        try:
            os.remove("test_support.db")
        except (PermissionError, FileNotFoundError):
            pass
    if os.path.exists("test_attachments"):
        shutil.rmtree("test_attachments", ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_adapters():
    reset_adapters()
    reset_state()
    yield
    reset_adapters()
    reset_state()


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
def defaults(session, tenant_id):
    """Global + tenant default catalogue (queues, teams, calendar, SLA)."""
    catalog_service.ensure_global_defaults(session)
    catalog_service.ensure_tenant_defaults(session, tenant_id)
    session.commit()
    return tenant_id


def make_token(role: str = "SUPPORT_MANAGER", tenant: uuid.UUID | None = None, extra: dict | None = None) -> str:
    claims = {
        "userId": "test-agent",
        "role": role,
        "permissions": [],
        "tenant_id": str(tenant) if tenant else None,
        ** (extra or {}),
    }
    return jwt.encode(claims, os.environ["SUPPORT_JWT_SECRET"], algorithm="HS256")


def make_customer_token(customer_id: str = "CUST-0001", tenant: uuid.UUID | None = None) -> str:
    claims = {
        "sub": customer_id,
        "customer_id": customer_id,
        "tenant_id": str(tenant) if tenant else None,
        "role": "CUSTOMER",
    }
    return jwt.encode(claims, os.environ["SUPPORT_CUSTOMER_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def auth_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}


@pytest.fixture
def customer_headers(tenant_id):
    return {"Authorization": f"Bearer {make_customer_token('CUST-0001', tenant_id)}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": os.environ["SUPPORT_INTERNAL_API_KEY"]}


def ticket_payload(tenant_id, **overrides) -> dict:
    payload = {
        "tenant_id": str(tenant_id),
        "ticket_type": "CONNECTIVITY_ISSUE",
        "subject": "No internet at home",
        "description": "Customer reports no internet connectivity since this morning.",
        "customer_id": "CUST-0001",
        "customer_number": "CUST-0001",
        "customer_name": "Test Customer",
        "service_subscription_id": "SUB-0001",
        "subscriber_username": "subs-0001",
        "service_location_id": "loc-1",
        "source_channel": "CUSTOMER_PORTAL",
        "category_code": "CONNECTIVITY",
        "impact": "HIGH",
        "urgency": "HIGH",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def make_ticket(session, tenant_id, defaults):
    def _make(**overrides):
        payload = ticket_payload(tenant_id, **overrides)
        payload.pop("tenant_id", None)  # tenant_id is passed positionally to the service
        ticket = ticket_service.create_ticket(session, tenant_id, **payload)
        session.commit()
        session.refresh(ticket)
        return ticket

    return _make
