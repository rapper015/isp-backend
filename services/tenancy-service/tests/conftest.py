"""Hermetic test environment for the Tenancy Service (Milestone 8)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_tenancy.db")
os.environ.setdefault("TENANCY_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("TENANCY_JWT_SECRET", "test-tenancy-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("TENANCY_ENCRYPTION_KEY", "test-encryption-key-0123456789abcdef0123456789ab")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")

import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.models import Tenant  # noqa: E402
from app.services import catalog_service, tenant_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_tenancy.db", "tenancy.db"):
        for suffix in ("", "-wal", "-shm"):
            full = path + suffix
            if os.path.exists(full):
                try:
                    os.remove(full)
                except (PermissionError, FileNotFoundError):
                    pass
    Base.metadata.create_all(bind=engine)
    yield
    for suffix in ("", "-wal", "-shm"):
        full = "test_tenancy.db" + suffix
        if os.path.exists(full):
            try:
                os.remove(full)
            except (PermissionError, FileNotFoundError):
                pass


@pytest.fixture(autouse=True)
def _clean_db():
    """Truncate every table before each test so the shared SQLite file cannot
    leak rows across tests."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def defaults(session):
    catalog_service.ensure_defaults(session)
    session.commit()
    return True


def make_token(role: str = "PLATFORM_ADMIN", tenant: uuid.UUID | None = None,
               scope_kind: str | None = None, extra: dict | None = None) -> str:
    claims = {
        "userId": "test-agent",
        "role": role,
        "permissions": [],
        "tenant_id": str(tenant) if tenant else None,
        "scope_kind": scope_kind,
        ** (extra or {}),
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None},
                      os.environ["TENANCY_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def tenant_id(defaults, session) -> uuid.UUID:
    tenant = Tenant(name="Test ISP", code=f"TEST-{uuid.uuid4().hex[:8].upper()}")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant.id


@pytest.fixture
def tenant(defaults, session) -> Tenant:
    """An ACTIVE, provisioned tenant."""
    created = tenant_service.create_tenant(
        session, name="Active ISP", code=f"ACT-{uuid.uuid4().hex[:8].upper()}")
    session.commit()
    tenant_service.provision_tenant(session, created.id)
    session.commit()
    session.refresh(created)
    return created


@pytest.fixture
def tenant_b(defaults, session) -> Tenant:
    created = tenant_service.create_tenant(session, name="Second ISP",
                                           code=f"SEC-{uuid.uuid4().hex[:8].upper()}")
    session.commit()
    tenant_service.provision_tenant(session, created.id)
    session.commit()
    session.refresh(created)
    return created


@pytest.fixture
def auth_headers(tenant):
    return {"Authorization": f"Bearer {make_token('TENANT_ADMIN', tenant.id)}"}


@pytest.fixture
def platform_headers():
    return {"Authorization": f"Bearer {make_token('PLATFORM_ADMIN')}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": os.environ["TENANCY_INTERNAL_API_KEY"]}


@pytest.fixture
def make_partner(session, tenant):
    from app.services import organization_service

    def _make(code=None, partner_type="FRANCHISE"):
        code = code or f"PTN-{uuid.uuid4().hex[:6].upper()}"
        partner = organization_service.create_partner(session, tenant.id, partner_type=partner_type,
                                                      code=code, name=code)
        session.commit()
        organization_service.change_partner_status(session, tenant.id, partner.id,
                                                   to_status="ONBOARDING", reason="onboard")
        organization_service.change_partner_status(session, tenant.id, partner.id,
                                                   to_status="ACTIVE", reason="onboarded")
        session.commit()
        return partner
    return _make


@pytest.fixture
def make_commission_plan(session, tenant):
    from app.services import commission_service

    def _make(code=None, basis="PAYMENT_COLLECTION", calc="PERCENTAGE", rate=10.0):
        code = code or f"PLAN-{uuid.uuid4().hex[:6].upper()}"
        plan = commission_service.create_plan(session, tenant.id, code=code, name=code)
        session.commit()
        rule = commission_service.add_rule(session, tenant.id, plan.id, code=f"R-{code}",
                                           name="rule", basis=basis, calculation_type=calc, rate=rate)
        session.commit()
        commission_service.approve_plan(session, tenant.id, plan.id, approved_by="test")
        session.commit()
        return plan, rule
    return _make
