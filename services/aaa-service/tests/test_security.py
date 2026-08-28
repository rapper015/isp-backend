import asyncio
from starlette.requests import Request
from app.security import internal_service_auth

def request(headers):
    return Request({"type": "http", "method": "POST", "path": "/internal/radius/v1/authenticate", "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()], "client": ("127.0.0.1", 1234), "scheme": "http", "server": ("test", 80)})

def test_internal_api_key_rotation_accepts_each_active_key(monkeypatch):
    monkeypatch.setenv("AAA_INTERNAL_API_KEYS", "old-key,new-key")
    asyncio.run(internal_service_auth(request({"X-AAA-Service-Key": "new-key"})))
