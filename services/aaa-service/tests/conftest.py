"""Hermetic test environment for the AAA service.

Environment variables are configured before any ``app`` module is imported so
the engine, encryption and auth settings are stable for the whole session. A
session-scoped autouse fixture removes the test database before the first test
so the suite never accumulates state across runs.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_aaa.db")
os.environ.setdefault("AAA_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("AAA_INTERNAL_API_KEYS", "test-internal-key")
os.environ.setdefault("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
os.environ.setdefault("NAS_APPROVED_NETWORKS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,10.50.0.0/16,198.51.100.0/24,2001:db8::/32")
os.environ.setdefault("AAA_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("AAA_ROUTEROS_ADAPTER", "fake")

import pytest  # noqa: E402

from uuid import uuid4  # noqa: E402
import bcrypt  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.models import Credential, Nas, NasCredential, Tenant  # noqa: E402
from app.security import encrypt_secret  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    """Remove any previous test database so the suite is deterministic."""
    for path in ("test_aaa.db", "aaa.db"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                pass
    yield
    if os.path.exists("test_aaa.db"):
        try:
            os.remove("test_aaa.db")
        except PermissionError:
            pass


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def tenant(session) -> Tenant:
    t = Tenant(name=f"Tenant {uuid4().hex[:6]}", enabled=True)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@pytest.fixture
def tenant_id(tenant):
    return tenant.id


@pytest.fixture
def nas(session, tenant) -> Nas:
    item = Nas(
        tenant_id=tenant.id,
        name="Edge CCR2004",
        source_ip="10.50.0.1",
        management_host="10.50.0.1",
        management_port=8729,
        management_protocol="api_ssl",
        api_mode="auto",
        tls_verify=True,
        vendor="mikrotik",
        device_type="router",
        routeros_version="7.15",
        allowed_services=["pppoe", "hotspot"],
        enabled=True,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@pytest.fixture
def nas_credential(session, nas) -> NasCredential:
    item = NasCredential(
        nas_id=nas.id,
        username_ciphertext=encrypt_secret("admin"),
        secret_ciphertext=encrypt_secret("not-a-real-secret"),
        credential_type="password",
        api_port=8729,
        status="active",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@pytest.fixture
def subscriber(session, tenant) -> Credential:
    item = Credential(
        tenant_id=tenant.id,
        subscriber_id=uuid4(),
        username="cust-a",
        username_normalized="cust-a",
        password_hash=bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode(),
        status="active",
        allowed_methods=["pap", "mschapv2"],
        mac_address=None,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
