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
