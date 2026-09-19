import httpx

from app.integrations.bss_client import BssClient


def test_live_plan_validation_uses_bss_service(monkeypatch):
    monkeypatch.setenv("OSS_BSS_PLAN_VALIDATION_MODE", "live")
    monkeypatch.setenv("OSS_BSS_BASE_URL", "http://bss.test")
    requested_urls = []

    def fake_get(url, timeout):
        requested_urls.append((url, timeout))
        return httpx.Response(200, json={"id": "plan-1", "plan_code": "HOME-100", "status": "active"})

    monkeypatch.setattr("app.integrations.bss_client.httpx.get", fake_get)
    result = BssClient().validate_plan("plan-1")

    assert result.ok is True
    assert result.checks["plan_code"] == "HOME-100"
    assert requested_urls == [("http://bss.test/plans/plan-1", 5.0)]


def test_live_plan_validation_reports_missing_or_inactive_plan(monkeypatch):
    monkeypatch.setenv("OSS_BSS_PLAN_VALIDATION_MODE", "live")
    monkeypatch.setenv("OSS_BSS_BASE_URL", "http://bss.test")

    monkeypatch.setattr("app.integrations.bss_client.httpx.get", lambda *_args, **_kwargs: httpx.Response(404))
    missing = BssClient().validate_plan("missing")
    assert missing.ok is False
    assert missing.errors == ["selected plan was not found in BSS"]

    monkeypatch.setattr("app.integrations.bss_client.httpx.get", lambda *_args, **_kwargs: httpx.Response(200, json={"status": "inactive"}))
    inactive = BssClient().validate_plan("inactive")
    assert inactive.ok is False
    assert inactive.errors == ["selected plan is inactive"]
