"""AAA Milestone 0 auth: user creation + login issuing platform JWTs."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

SERVICE = {"X-AAA-Service-Key": "test-internal-key"}
SECRET = "test-aaa-jwt-secret-at-least-32-bytes-long"


def _username(tag):
    return f"{tag}-{uuid4().hex[:8]}"


def _create(client, username, password="Str0ng!Passw0rd", role="PLATFORM_ADMIN"):
    return client.post("/api/aaa/users", headers=SERVICE, json={
        "username": username, "password": password, "full_name": "Test User",
        "email": f"{username}@isp.test", "role": role})


def test_create_and_list_users(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        username = _username("creator")
        r = _create(client, username)
        assert r.status_code == 201
        assert r.json()["role"] == "PLATFORM_ADMIN"
        listing = client.get("/api/aaa/users", headers=SERVICE).json()
        assert any(u["username"] == username for u in listing)


def test_login_issues_token_and_me(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        username = _username("login")
        _create(client, username)
        login = client.post("/api/aaa/login", json={"username": username, "password": "Str0ng!Passw0rd"})
        assert login.status_code == 200
        body = login.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["username"] == username
        me = client.get("/api/aaa/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["username"] == username
        assert me.json()["role"] == "PLATFORM_ADMIN"


def test_admin_login_alias(monkeypatch):
    """nginx maps /api/v1/auth/login -> /admin-login."""
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        username = _username("alias")
        _create(client, username)
        login = client.post("/admin-login", json={"username": username, "password": "Str0ng!Passw0rd"})
        assert login.status_code == 200
        assert login.json()["access_token"]


def test_login_wrong_password_rejected(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        username = _username("badpwd")
        _create(client, username)
        login = client.post("/api/aaa/login", json={"username": username, "password": "wrong-password"})
        assert login.status_code == 401


def test_create_user_requires_service_key(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        r = client.post("/api/aaa/users", json={
            "username": _username("noauth"), "password": "Str0ng!Passw0rd", "role": "READ_ONLY"})
        assert r.status_code == 401


def test_duplicate_username_conflict(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    with TestClient(app) as client:
        username = _username("dup")
        assert _create(client, username).status_code == 201
        assert _create(client, username).status_code == 409


def test_bootstrap_admin_from_env(monkeypatch):
    monkeypatch.setenv("AAA_JWT_SECRET", SECRET)
    monkeypatch.setenv("AAA_BOOTSTRAP_ADMIN_USERNAME", "root-admin")
    monkeypatch.setenv("AAA_BOOTSTRAP_ADMIN_PASSWORD", "Bootstr@pHard1")
    with TestClient(app) as client:
        login = client.post("/api/aaa/login", json={"username": "root-admin", "password": "Bootstr@pHard1"})
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "PLATFORM_ADMIN"
