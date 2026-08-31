from app.database import SessionLocal
from app.main import assign_roles, ensure_foundations
from app.models import PlatformUser
from app.security import hash_password
def bootstrap(session):
    ensure_foundations(session); user = PlatformUser(username="admin", username_normalized="admin", password_hash=hash_password("VeryStrong!Password1")); session.add(user); session.flush(); assign_roles(session, user, ["PLATFORM_SUPER_ADMIN"]); session.commit()
def login(client): return client.post("/api/v1/auth/login", json={"username":"admin", "password":"VeryStrong!Password1"})
def test_rotation_logout_and_password_change(client):
    with SessionLocal() as session: bootstrap(session)
    first = login(client); assert first.status_code == 200
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}); assert refreshed.status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}).status_code == 401
    access = refreshed.json()["access_token"]
    assert client.post("/api/v1/auth/change-password", headers={"Authorization":f"Bearer {access}"}, json={"current_password":"VeryStrong!Password1", "new_password":"NewVeryStrong!Password2"}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refreshed.json()["refresh_token"]}).status_code == 401
def test_subscriber_like_credentials_cannot_login(client):
    assert client.post("/api/v1/auth/login", json={"username":"pppoe-subscriber", "password":"subscriber-password"}).status_code == 401
def test_email_login_and_disabled_account_rejection(client):
    with SessionLocal() as session:
        bootstrap(session)
        user = session.query(PlatformUser).filter_by(username="admin").one(); user.email = "admin@isp.test"; session.commit()
    assert client.post("/api/v1/auth/login", json={"username":"admin@isp.test", "password":"VeryStrong!Password1"}).status_code == 200
    with SessionLocal() as session:
        user = session.query(PlatformUser).filter_by(username="admin").one(); user.enabled = False; session.commit()
    assert login(client).status_code == 401
def test_service_account_secret_is_one_time_visible_and_can_get_token(client):
    with SessionLocal() as session: bootstrap(session)
    access = login(client).json()["access_token"]
    account = client.post("/api/v1/platform/service-accounts", headers={"Authorization": f"Bearer {access}"}, json={"name":"crm-worker", "permissions":["crm.customers.read"]})
    assert account.status_code == 201
    assert "api_key" in account.json()
    issued = client.post("/internal/auth/service-token", headers={"X-Platform-Service-Key": account.json()["api_key"]})
    assert issued.status_code == 200
    assert issued.json()["access_token"]
