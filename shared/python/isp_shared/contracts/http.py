"""Small, safe transport used by typed internal service clients."""
from __future__ import annotations

import json
from typing import Any, TypeVar
from urllib import error as urlerror, request as urlrequest

from isp_shared.observability import diagnostic_headers
from pydantic import BaseModel, ValidationError


ContractModel = TypeVar("ContractModel", bound=BaseModel)


class ServiceContractError(RuntimeError):
    def __init__(
        self,
        service: str,
        status_code: int,
        message: str,
        *,
        code: str = "UPSTREAM_REQUEST_FAILED",
        retryable: bool | None = None,
        correlation_id: str | None = None,
    ):
        super().__init__(message)
        self.service = service
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retryable = status_code >= 500 if retryable is None else retryable
        self.correlation_id = correlation_id


class JsonServiceClient:
    def __init__(self, service: str, base_url: str, headers: dict[str, str] | None = None):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 10,
        headers: dict[str, str] | None = None,
    ) -> dict:
        upstream = urlrequest.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={**self.headers, **(headers or {}), **diagnostic_headers()},
            method=method,
        )
        try:
            with urlrequest.urlopen(upstream, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except ValueError as error:
                    raise ServiceContractError(
                        self.service,
                        502,
                        f"{self.service} returned an invalid response",
                        code="UPSTREAM_CONTRACT_INVALID",
                        retryable=False,
                    ) from error
        except urlerror.HTTPError as error:
            raw = error.read().decode(errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            detail = body.get("message") or body.get("detail") or "upstream service rejected the request"
            if not isinstance(detail, str):
                detail = "upstream service rejected the request"
            raise ServiceContractError(
                self.service,
                error.code,
                detail,
                code=str(body.get("code") or "UPSTREAM_REQUEST_REJECTED"),
                retryable=body.get("retryable") if isinstance(body.get("retryable"), bool) else None,
                correlation_id=body.get("correlation_id"),
            ) from error
        except (urlerror.URLError, TimeoutError, OSError) as error:
            raise ServiceContractError(
                self.service,
                502,
                f"{self.service} is unavailable",
                code="UPSTREAM_UNAVAILABLE",
                retryable=True,
            ) from error


def validate_response(service: str, model: type[ContractModel], body: dict) -> ContractModel:
    try:
        return model.model_validate(body)
    except ValidationError as error:
        raise ServiceContractError(
            service,
            502,
            f"{service} returned an incompatible response",
            code="UPSTREAM_CONTRACT_INVALID",
            retryable=False,
        ) from error
