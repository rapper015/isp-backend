import os, sys
from pathlib import Path
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-platform-core.db")
os.environ.setdefault("PLATFORM_JWT_SECRET", "test-platform-jwt-secret-at-least-32-characters-long")
os.environ.setdefault("PLATFORM_BOOTSTRAP_ADMIN_USERNAME", "")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app
@pytest.fixture
def client():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with TestClient(app) as value: yield value
