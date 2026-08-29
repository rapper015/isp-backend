"""Shared thin HTTP client plumbing for support adapters."""
from __future__ import annotations

import os
from typing import Any

import httpx

from .base import ActionResult, NonRetryableAdapterError, RetryableAdapterError, StepResult, ok_result


class _HttpClient:
    """Minimal gateway client. Fails open with retryable errors on any
    connection/HTTP problem so diagnostics never fabricate healthy data."""

    def __init__(self, base_url_env: str, api_key_env: str | None = None):
        self.base_url = os.getenv(base_url_env, "").rstrip("/")
        self.api_key = os.getenv(api_key_env, "")

    def active(self) -> bool:
        return bool(self.base_url)

    def _headers(self, correlation_id: str | None = None) -> dict:
        headers = {"X-Correlation-Id": correlation_id or ""}
        if self.api_key:
            headers["X-Internal-API-Key"] = self.api_key
        return headers

    def get_json(self, path: str, *, correlation_id: str | None = None, timeout: float = 2.0) -> dict:
        if not self.active():
            raise RetryableAdapterError("adapter not configured (base URL missing)")
        try:
            response = httpx.get(f"{self.base_url}{path}", headers=self._headers(correlation_id), timeout=timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 401, 403, 404, 422):
                raise NonRetryableAdapterError(f"upstream rejected request: {error.response.status_code}")
            raise RetryableAdapterError(f"upstream error: {error.response.status_code}") from error
        except (httpx.HTTPError, OSError) as error:
            raise RetryableAdapterError(f"upstream unavailable: {error}") from error

    def post_json(self, path: str, body: dict | None = None, *, correlation_id: str | None = None, timeout: float = 3.0) -> dict:
        if not self.active():
            raise RetryableAdapterError("adapter not configured (base URL missing)")
        try:
            response = httpx.post(f"{self.base_url}{path}", json=body or {}, headers=self._headers(correlation_id), timeout=timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 401, 403, 404, 422):
                raise NonRetryableAdapterError(f"upstream rejected request: {error.response.status_code}")
            raise RetryableAdapterError(f"upstream error: {error.response.status_code}") from error
        except (httpx.HTTPError, OSError) as error:
            raise RetryableAdapterError(f"upstream unavailable: {error}") from error
