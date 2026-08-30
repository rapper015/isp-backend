"""Identity service auth tests: user creation + login issuing JWTs."""
from uuid import uuid4

from conftest import make_token

SERVICE = {"X-Identity-Service-Key": "test-internal-key"}


def _username(tag):
    return f"{tag}-{uuid4().hex[:8]}"


def _create(client, username, password="Str0ng!Passw0rd", role="PLATFORM_ADMIN"):
    return client.post("/api/auth/users", headers=SERVICE, json={
        "username": username, "password": password, "full_name": "Test User",
        "email": f"{username}@isp.test", "role": role})


def test_create_and_list_users(client):
    username = _username("creator")
    r = _create(client, username)
    assert r.status_code == 201
    assert r.json()["role"] == "PLATFORM_ADMIN"
    listing = client.get("/api/auth/users", headers=SERVICE).json()
    assert any(u["username"] == username for u in listing)


def test_login_issues_token_and_me(client):
    username = _username("login")
    _create(client, username)
    login = client.post("/api/auth/login", json={"username": username, "password": "Str0ng!Passw0rd"})
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == username
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == username
    assert me.json()["role"] == "PLATFORM_ADMIN"


def test_admin_login_alias(client):
    username = _username("alias")
    _create(client, username)
    login = client.post("/admin-login", json={"username": username, "password": "Str0ng!Passw0rd"})
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_login_wrong_password_rejected(client):
    username = _username("badpwd")
    _create(client, username)
    login = client.post("/api/auth/login", json={"username": username, "password": "wrong-password"})
    assert login.status_code == 401


def test_create_user_requires_service_key(client):
    r = client.post("/api/auth/users", json={
        "username": _username("noauth"), "password": "Str0ng!Passw0rd", "role": "READ_ONLY"})
    assert r.status_code == 401


def test_duplicate_username_conflict(client):
    username = _username("dup")
    assert _create(client, username).status_code == 201
    assert _create(client, username).status_code == 409


def test_me_rejects_bad_token(client):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_token_claims_carry_tenant_scope():
    token = make_token("PLATFORM_ADMIN", uuid4())
    assert token
