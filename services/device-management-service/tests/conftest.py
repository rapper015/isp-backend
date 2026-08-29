"""Hermetic test environment for the device-management service (Milestone 7)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_device.db")
os.environ.setdefault("DEVICE_MANAGEMENT_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("DEVICE_MANAGEMENT_JWT_SECRET", "test-device-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("DEVICE_MANAGEMENT_ENCRYPTION_KEY", "test-encryption-key-0123456789abcdef0123456789ab")
os.environ.setdefault("DEVICE_MANAGEMENT_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("ACS_PROVIDER", "fake")

import shutil  # noqa: E402
import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.integrations.acs import FakeACSClient, get_acs_client  # noqa: E402
from app.integrations.base import reset_adapters  # noqa: E402
from app.integrations.fakes import reset_state  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.services import catalog_service, device_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_device.db", "device_management.db"):
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
        full = "test_device.db" + suffix
        if os.path.exists(full):
            try:
                os.remove(full)
            except (PermissionError, FileNotFoundError):
                pass


@pytest.fixture(autouse=True)
def _reset_adapters():
    reset_adapters()
    reset_state()
    FakeACSClient.reset()
    yield
    reset_adapters()
    reset_state()
    FakeACSClient.reset()


@pytest.fixture(autouse=True)
def _clean_db():
    """Truncate every table before each test so the shared SQLite file cannot
    leak rows (serials, tenants) across tests."""
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
def tenant_id(session) -> uuid.UUID:
    tenant = Tenant(name="Test ISP", code=f"TEST-{uuid.uuid4().hex[:8].upper()}")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant.id


@pytest.fixture
def tenant_b(session) -> uuid.UUID:
    tenant = Tenant(name="Second ISP", code=f"SEC-{uuid.uuid4().hex[:6].upper()}")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant.id


@pytest.fixture
def defaults(session):
    """Seed the global device-model catalogue."""
    catalog_service.ensure_global_defaults(session)
    session.commit()
    return True


def make_token(role: str = "ISP_ADMIN", tenant: uuid.UUID | None = None, extra: dict | None = None) -> str:
    claims = {
        "userId": "test-agent",
        "role": role,
        "permissions": [],
        "tenant_id": str(tenant) if tenant else None,
        ** (extra or {}),
    }
    return jwt.encode(claims, os.environ["DEVICE_MANAGEMENT_JWT_SECRET"], algorithm="HS256")


def variant_for(session, *, model_name: str | None = None, data_model_family: str | None = None):
    """Look up a DeviceModelVariant by model name or data-model family."""
    from app.models import DeviceModel, DeviceModelVariant

    query = session.query(DeviceModelVariant).join(
        DeviceModel, DeviceModel.id == DeviceModelVariant.model_id)
    if model_name:
        query = query.filter(DeviceModel.model_name == model_name)
    if data_model_family:
        query = query.filter(DeviceModelVariant.data_model_family == data_model_family)
    return query.first()


@pytest.fixture
def auth_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('ISP_ADMIN', tenant_id)}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": os.environ["DEVICE_MANAGEMENT_INTERNAL_API_KEY"]}


@pytest.fixture
def acs(session, defaults):
    """A fake ACS client with a seeded device, registered ACS instance + binding."""
    from app.models import ACSInstance

    instance = ACSInstance(tenant_id=None, name="acs-test", base_url="http://genieacs:7557",
                           environment="TEST", health="HEALTHY", is_active=True)
    session.add(instance)
    session.commit()
    session.refresh(instance)
    client = get_acs_client({"instance_id": str(instance.id)})
    return {"instance": instance, "client": client}


@pytest.fixture
def make_acs_device(acs):
    """Seed a device in the fake ACS and return its acs_device_id."""
    def _make(**kwargs):
        return acs["client"].seed_device(**kwargs)
    return _make


@pytest.fixture
def make_device(session, tenant_id, acs, make_acs_device):
    """Discover + claim a device in the fake ACS for the tenant."""
    def _make(*, serial="SN0001", oui="A4B1C1", product_class="AN5506",
              method="PREREGISTERED_SERIAL", claim: bool = True):
        from app.integrations.fakes import STATE

        if method == "PREREGISTERED_SERIAL":
            STATE.seed_inventory_asset(serial)
        acs_device_id = make_acs_device(serial_number=serial, oui=oui, product_class=product_class)
        device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                                  requested_tenant_id=tenant_id, actor="test")
        session.commit()
        if claim:
            device = device_service.claim_device(session, tenant_id, device.id, method=method,
                                                 evidence="test evidence", actor="test")
            session.commit()
            session.refresh(device)
        return device, acs_device_id
    return _make


@pytest.fixture
def make_profile(session, tenant_id, defaults):
    def _make(code="FIBER_HOME", definition=None, activate=True):
        from app.services import profile_service

        profile = profile_service.create_profile(session, tenant_id, code=code, name=code.replace("_", " ").title())
        session.commit()
        definition = definition or {
            "WIFI_SSID_24GHZ": {"value": "TestNet", "writable": True},
            "WIFI_PASSWORD_24GHZ": {"value": "supersecret", "writable": True, "sensitive": True},
            "VLAN_ID": {"value": 100, "writable": True},
            "PERIODIC_INFORM_INTERVAL": {"value": 60, "writable": True},
        }
        version = profile_service.create_version(session, tenant_id, profile.id, definition=definition,
                                                 actor="test")
        session.commit()
        if activate:
            profile_service.approve_version(session, tenant_id, version.id, actor="test")
            version = profile_service.activate_version(session, tenant_id, version.id, actor="test")
            session.commit()
            session.refresh(version)
        return profile, version
    return _make
