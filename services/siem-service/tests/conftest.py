"""Hermetic test environment for the SIEM service (Master Spec Batch 1)."""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_siem.db")
os.environ.setdefault("SIEM_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("SIEM_JWT_SECRET", "test-siem-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("SIEM_ENCRYPTION_KEY", "test-siem-encryption-key-0123456789abcdef")

import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_siem.db", "siem.db"):
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
        full = "test_siem.db" + suffix
        if os.path.exists(full):
            try:
                os.remove(full)
            except (PermissionError, FileNotFoundError):
                pass


@pytest.fixture(autouse=True)
def _clean_db():
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


def make_token(role: str = "PLATFORM_ADMIN", tenant: uuid.UUID | None = None,
               scope_kind: str | None = None, permissions: list | None = None) -> str:
    claims = {
        "userId": "test-agent",
        "role": role,
        "permissions": permissions or [],
        "tenant_id": str(tenant) if tenant else None,
        "scope_kind": scope_kind,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None},
                      os.environ["SIEM_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def tenant_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('TENANT_ADMIN', tenant_id)}"}


@pytest.fixture
def soc_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('SECURITY_OPS', tenant_id)}"}


@pytest.fixture
def auditor_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('AUDITOR', tenant_id)}"}


@pytest.fixture
def platform_headers():
    return {"Authorization": f"Bearer {make_token('PLATFORM_ADMIN', scope_kind='PLATFORM_AGGREGATE')}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": "test-internal-key"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def ingest_event(client, headers, **overrides):
    body = {
        "event_type": "AUTH.LOGIN_FAILED",
        "category": "AUTH",
        "severity": "HIGH",
        "source_ip": "203.0.113.7",
        "actor": "sub-1001",
        "target": "radius-gw-1",
        "payload": {"username": "alice@example.com", "attempts": 3},
        "tags": ["auth", "bruteforce"],
    }
    body.update(overrides)
    return client.post("/api/siem/v1/security-events", json=body, headers=headers)
