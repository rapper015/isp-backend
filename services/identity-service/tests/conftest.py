"""Hermetic test environment for the Identity service."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_identity.db")
os.environ.setdefault("IDENTITY_JWT_SECRET", "test-identity-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("IDENTITY_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("IDENTITY_INTERNAL_API_KEYS", "test-internal-key")

import uuid  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401


@pytest.fixture(scope="session", autouse=True)
def _database():
    for path in ("test_identity.db", "identity.db"):
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
        full = "test_identity.db" + suffix
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


def make_token(role: str = "PLATFORM_ADMIN", tenant: uuid.UUID | None = None) -> str:
    claims = {"userId": str(uuid.uuid4()), "role": role, "permissions": [],
              "tenant_id": str(tenant) if tenant else None}
    return jwt.encode({k: v for k, v in claims.items() if v is not None},
                      os.environ["IDENTITY_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
