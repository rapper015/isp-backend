"""Hermetic test environment for the BSS service (Milestone 4)."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_bss.db")
os.environ.setdefault("BSS_INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("BSS_INTERNAL_API_KEYS", "test-internal-key")
os.environ.setdefault("BSS_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
os.environ.setdefault("BSS_JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("BSS_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("VALKEY_URL", "redis://127.0.0.1:6399/0")

import uuid  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402, F401
import app.revenue.models  # noqa: E402, F401
from app.revenue.models import BillingAccount, GatewayAccount, RevenueInvoice, Tenant  # noqa: E402
from app.security import encrypt_secret  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    for path in ("test_bss.db", "bss.db"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                pass
    yield
    if os.path.exists("test_bss.db"):
        try:
            os.remove("test_bss.db")
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
    t = Tenant(name=f"Tenant {uuid.uuid4().hex[:6]}", code=f"T{uuid.uuid4().hex[:8].upper()}", currency="INR")
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@pytest.fixture
def tenant_id(tenant):
    return tenant.id


@pytest.fixture
def account(session, tenant) -> BillingAccount:
    a = BillingAccount(tenant_id=tenant.id, account_code=f"ACC-{uuid.uuid4().hex[:8].upper()}", customer_ref=f"cust-{uuid.uuid4().hex[:8]}", currency="INR", status="ACTIVE")
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@pytest.fixture
def gateway(session, tenant, webhook_secret="whsec-test-0001") -> GatewayAccount:
    g = GatewayAccount(
        tenant_id=tenant.id,
        code="fake-test",
        gateway_code="FAKE",
        mode="test",
        api_key_ciphertext=encrypt_secret("fake_api_key"),
        secret_ciphertext=encrypt_secret("fake_secret"),
        webhook_secret_ciphertext=encrypt_secret(webhook_secret),
        currency="INR",
        methods=["CARD", "UPI"],
        is_default=True,
        priority=1,
        status="ACTIVE",
    )
    session.add(g)
    session.commit()
    session.refresh(g)
    return g


def make_invoice(session, tenant, account, *, amount="1000.00", number=None, due_offset_days=30) -> RevenueInvoice:
    from datetime import datetime, timedelta, timezone

    inv = RevenueInvoice(
        tenant_id=tenant.id,
        billing_account_id=account.id,
        invoice_number=number or f"INV-{uuid.uuid4().hex[:8].upper()}",
        currency="INR",
        total_amount=Decimal(amount),
        paid_amount=Decimal("0.00"),
        written_off_amount=Decimal("0.00"),
        status="ISSUED",
        issued_at=datetime.now(timezone.utc),
        due_date=datetime.now(timezone.utc) + timedelta(days=due_offset_days),
    )
    session.add(inv)
    session.commit()
    session.refresh(inv)
    return inv


@pytest.fixture
def invoice(session, tenant, account) -> RevenueInvoice:
    return make_invoice(session, tenant, account)
