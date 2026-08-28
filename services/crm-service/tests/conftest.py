"""Hermetic test environment for the CRM service."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_crm.db")
os.environ.setdefault("CRM_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("CRM_INTERNAL_API_KEYS", "test-internal-key")
os.environ.setdefault("CRM_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
os.environ.setdefault("CRM_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("VALKEY_URL", "redis://127.0.0.1:6379/0")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    for path in ("test_crm.db", "crm.db"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                pass
    yield
    if os.path.exists("test_crm.db"):
        try:
            os.remove("test_crm.db")
        except PermissionError:
            pass
