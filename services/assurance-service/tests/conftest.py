"""Hermetic test environment for the Assurance Service (Milestone 9)."""
import os
import sys
from pathlib import Path

# Make the monorepo root importable so `shared.python.isp_shared` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_assurance.db")
os.environ.setdefault("ASSURANCE_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("ASSURANCE_JWT_SECRET", "test-assurance-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")

import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
from app.services import catalog_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_assurance.db", "assurance.db"):
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
        full = "test_assurance.db" + suffix
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


@pytest.fixture
def tenant_id(defaults, session) -> uuid.UUID:
    return uuid.uuid4()


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
                      os.environ["ASSURANCE_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def tenant_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('TENANT_ADMIN', tenant_id)}"}


@pytest.fixture
def platform_headers():
    return {"Authorization": f"Bearer {make_token('PLATFORM_ADMIN', scope_kind='PLATFORM_AGGREGATE')}"}


@pytest.fixture
def sre_headers(tenant_id):
    return {"Authorization": f"Bearer {make_token('SRE_PLATFORM', tenant_id)}"}


@pytest.fixture
def internal_headers():
    return {"X-Internal-API-Key": "test-internal-key"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
