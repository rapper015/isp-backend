"""The only HTTP boundary used by CRM customer onboarding to call OSS."""
import json
from os import getenv
from urllib import error as urlerror, request as urlrequest

from isp_shared.observability import diagnostic_headers


class OssRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class OssProvisioningClient:
    def __init__(self, service_name: str):
        self.base_url = getenv("CRM_OSS_BASE_URL", "http://oss-service:8000").rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
            "X-Internal-API-Key": getenv("CRM_OSS_INTERNAL_API_KEY", ""),
            "X-Service-Name": service_name,
        }

    def _post(self, path: str, payload: dict, timeout: int) -> dict:
        upstream = urlrequest.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={**self.headers, **diagnostic_headers()},
            method="POST",
        )
        try:
            with urlrequest.urlopen(upstream, timeout=timeout) as response:
                return json.loads(response.read())
        except urlerror.HTTPError as error:
            raw = error.read().decode()
            try:
                detail = json.loads(raw).get("detail", raw)
            except (ValueError, AttributeError):
                detail = raw
            raise OssRequestError(error.code, str(detail or "OSS request failed")) from error
        except urlerror.URLError as error:
            raise OssRequestError(502, "provisioning service is unavailable") from error

    def create_order(self, payload: dict) -> dict:
        return self._post("/api/oss/orders", payload, timeout=15)

    def activate_order(self, order_id: str) -> dict:
        return self._post(f"/api/oss/orders/{order_id}/activate", {}, timeout=30)
