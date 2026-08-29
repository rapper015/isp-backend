"""Hermetic test environment for the workforce service (Milestone 6)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_workforce.db")
os.environ.setdefault("WORKFORCE_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("WORKFORCE_JWT_SECRET", "test-workforce-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("WORKFORCE_TECHNICIAN_JWT_SECRET", "test-tech-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("WORKFORCE_CUSTOMER_JWT_SECRET", "test-customer-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("WORKFORCE_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("WORKFORCE_ATTACHMENT_DIR", "./test_workforce_attachments")
os.environ.setdefault("MAPS_PROVIDER", "fake")

import shutil  # noqa: E402
import uuid  # noqa: E402
from datetime import date, datetime, time, timedelta, timezone  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.integrations.fakes import reset_state  # noqa: E402
from app.integrations.base import reset_adapters  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.services import catalog_service, technician_service, workorder_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_workforce.db", "workforce.db"):
        for suffix in ("", "-wal", "-shm"):
            full = path + suffix
            if os.path.exists(full):
                try:
                    os.remove(full)
                except (PermissionError, FileNotFoundError):
                    pass
    if os.path.exists("test_workforce_attachments"):
        shutil.rmtree("test_workforce_attachments", ignore_errors=True)
    Base.metadata.create_all(bind=engine)
    yield
    for suffix in ("", "-wal", "-shm"):
        full = "test_workforce.db" + suffix
        if os.path.exists(full):
            try:
                os.remove(full)
            except (PermissionError, FileNotFoundError):
                pass
    if os.path.exists("test_workforce_attachments"):
        shutil.rmtree("test_workforce_attachments", ignore_errors=True)


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
    """Global + tenant default catalogue (types, templates, checklists, SLA)."""
    catalog_service.ensure_global_defaults(session)
    catalog_service.ensure_tenant_defaults(session, tenant_id)
    session.commit()
    return tenant_id


def make_token(role: str = "ISP_ADMIN", tenant: uuid.UUID | None = None, extra: dict | None = None) -> str:
    claims = {
        "userId": "test-agent",
        "role": role,
        "permissions": [],
        "tenant_id": str(tenant) if tenant else None,
        ** (extra or {}),
    }
    return jwt.encode(claims, os.environ["WORKFORCE_JWT_SECRET"], algorithm="HS256")


def make_technician_token(technician_id: str, tenant: uuid.UUID, *, device_ref: str | None = "dev-1",
                          extra: dict | None = None) -> str:
    claims = {
        "sub": f"tech:{technician_id}",
        "technician_id": technician_id,
        "tenant_id": str(tenant),
        "role": "TECHNICIAN",
        "device_ref": device_ref,
        ** (extra or {}),
    }
    return jwt.encode(claims, os.environ["WORKFORCE_TECHNICIAN_JWT_SECRET"], algorithm="HS256")


def make_customer_token(customer_id: str = "CUST-0001", tenant: uuid.UUID | None = None) -> str:
    claims = {
        "sub": customer_id,
        "customer_id": customer_id,
        "tenant_id": str(tenant) if tenant else None,
        "role": "CUSTOMER",
    }
    return jwt.encode(claims, os.environ["WORKFORCE_CUSTOMER_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def auth_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('ISP_ADMIN', tenant_id)}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": os.environ["WORKFORCE_INTERNAL_API_KEY"]}


def _today_shift():
    return time(0, 0), time(23, 59)


@pytest.fixture
def make_technician(session, tenant_id, defaults):
    def _make(name="Tech One", *, skills=None, certifications=None, capacity=4,
              service_area_ids=None, available=True, **profile_kwargs):
        technician = technician_service.create_technician(
            session, tenant_id, user_ref=f"user-{uuid.uuid4().hex[:6]}", name=name,
            employment_type=profile_kwargs.pop("employment_type", "EMPLOYEE"),
            team_code=profile_kwargs.pop("team_code", None),
            base_lat=profile_kwargs.pop("base_lat", 28.6139),
            base_lng=profile_kwargs.pop("base_lng", 77.2090),
            max_daily_capacity=capacity, service_area_ids=service_area_ids, actor="test")
        if skills:
            for skill in skills:
                technician_service.add_skill(session, tenant_id, technician.id, skill=skill, actor="test")
        if certifications:
            for cert in certifications:
                expires = None
                if isinstance(cert, dict):
                    expires = cert.get("expires_at")
                    cert = cert["certification"]
                technician_service.add_certification(session, tenant_id, technician.id,
                                                     certification=cert, expires_at=expires, actor="test")
        start, end = _today_shift()
        technician_service.set_shift(session, tenant_id, technician.id, day_of_week=date.today().weekday(),
                                     start_time=start, end_time=end, actor="test")
        if available:
            technician_service.set_availability(session, tenant_id, technician.id,
                                                available_date=date.today(), status="AVAILABLE", actor="test")
            technician_service.transition_status(session, tenant_id, technician.id, to_status="AVAILABLE",
                                                 source="TEST", actor="test")
        session.commit()
        session.refresh(technician)
        return technician

    return _make


@pytest.fixture
def make_work_order(session, tenant_id, defaults):
    def _make(work_order_type="NEW_INSTALLATION", **overrides):
        payload = {
            "work_order_type": work_order_type,
            "customer_id": "CUST-0001",
            "customer_name": "Test Customer",
            "service_subscription_id": "SUB-0001",
            "service_location_id": "loc-1",
            "latitude": 28.6139,
            "longitude": 77.2090,
            "address_line": "1 Main Rd",
            "priority": "P3_MEDIUM",
            "severity": "SEV3",
            "source_channel": "API",
        }
        payload.update(overrides)
        wo = workorder_service.create_work_order(session, tenant_id, **payload, actor="test")
        session.commit()
        session.refresh(wo)
        return wo

    return _make


def _aware(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@pytest.fixture
def tomorrow():
    return date.today() + timedelta(days=1)


@pytest.fixture
def next_weekday(tomorrow) -> date:
    d = tomorrow
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d
