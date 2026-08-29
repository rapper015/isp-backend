"""ACSClient interface plus GenieACSClient and FakeACSClient.

GenieACS owns CWMP sessions, the parameter tree, RPC execution and pending
tasks. This adapter is the only place GenieACS HTTP/NBI calls exist. The fake
is deterministic and stateful so tests drive task lifecycle without a live ACS."""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from ..domain.secrets import redact_log_line
from .base import AdapterError, NonRetryableAdapterError, RetryableAdapterError, ok_result


class ACSClient:
    name = "acs"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def health_check(self) -> dict:
        raise NotImplementedError

    def search_devices(self, *, query: str = "", limit: int = 100) -> list[dict]:
        raise NotImplementedError

    def get_device(self, device_id: str) -> dict | None:
        raise NotImplementedError

    def get_parameters(self, device_id: str, paths: list[str]) -> dict:
        raise NotImplementedError

    def refresh_object(self, device_id: str, path: str) -> str:
        raise NotImplementedError

    def set_parameters(self, device_id: str, parameters: dict) -> str:
        raise NotImplementedError

    def add_object(self, device_id: str, path: str) -> str:
        raise NotImplementedError

    def delete_object(self, device_id: str, path: str) -> str:
        raise NotImplementedError

    def reboot(self, device_id: str) -> str:
        raise NotImplementedError

    def factory_reset(self, device_id: str) -> str:
        raise NotImplementedError

    def download_file(self, device_id: str, url: str, file_type: str) -> str:
        raise NotImplementedError

    def create_task(self, device_id: str, name: str, *args, **kwargs) -> dict:
        raise NotImplementedError

    def get_task(self, task_id: str) -> dict:
        raise NotImplementedError

    def delete_task(self, task_id: str) -> None:
        raise NotImplementedError

    def trigger_connection_request(self, device_id: str, *, url: str | None = None) -> str:
        raise NotImplementedError

    def manage_tags(self, device_id: str, tags: list[str]) -> None:
        raise NotImplementedError

    def manage_presets(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        raise NotImplementedError

    def manage_provisions(self, name: str, *, script: str | None = None, delete: bool = False) -> None:
        raise NotImplementedError

    def manage_virtual_parameters(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        raise NotImplementedError

    def upload_file(self, name: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def delete_file(self, name: str) -> None:
        raise NotImplementedError


class GenieACSClient(ACSClient):
    """HTTP client for the GenieACS NBI. Configured base URL, TLS validation,
    timeouts, retries, connection pooling, circuit breaker and redacted logs."""

    name = "genieacs"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.base_url = (self.config.get("base_url") or "http://genieacs:7557").rstrip("/")
        self.timeout = float(self.config.get("timeout", 10))
        self.max_retries = int(self.config.get("max_retries", 3))
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout,
                                    verify=self.config.get("verify_tls", True))

    def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        self._guard()
        attempts = 0
        while True:
            attempts += 1
            try:
                response = self._client.request(method, path, json=json, params=params)
                self._record(response.status_code < 500, failure_threshold=5)
                if response.status_code >= 500:
                    raise RetryableAdapterError(f"ACS {response.status_code}", code="acs_server_error")
                if response.status_code >= 400:
                    raise NonRetryableAdapterError(
                        redact_log_line(response.text[:500]), code=f"acs_http_{response.status_code}")
                return response.json() if response.content else {}
            except (httpx.ConnectError, httpx.TimeoutException) as error:
                self._record(False, failure_threshold=5)
                if attempts >= self.max_retries:
                    raise RetryableAdapterError(f"ACS unreachable: {error}", code="acs_unreachable") from error
                time.sleep(0.2 * attempts)
            except AdapterError:
                raise

    def health_check(self) -> dict:
        data = self._request("GET", "/health")
        return {"ok": True, "version": data.get("version"), "detail": data}

    def search_devices(self, *, query: str = "", limit: int = 100) -> list[dict]:
        data = self._request("GET", "/devices", params={"query": query, "limit": limit})
        return data.get("devices", data if isinstance(data, list) else [])

    def get_device(self, device_id: str) -> dict | None:
        try:
            return self._request("GET", f"/devices/{device_id}")
        except NonRetryableAdapterError as error:
            if "404" in str(error):
                return None
            raise

    def get_parameters(self, device_id: str, paths: list[str]) -> dict:
        return self._request("GET", f"/devices/{device_id}/parameters", params={"path": paths})

    def refresh_object(self, device_id: str, path: str) -> str:
        data = self._request("POST", f"/devices/{device_id}/refresh", json={"path": path})
        return data.get("task_id", str(uuid.uuid4()))

    def set_parameters(self, device_id: str, parameters: dict) -> str:
        data = self._request("POST", f"/devices/{device_id}/parameters", json=parameters)
        return data.get("task_id", str(uuid.uuid4()))

    def add_object(self, device_id: str, path: str) -> str:
        data = self._request("POST", f"/devices/{device_id}/objects", json={"path": path})
        return data.get("task_id", str(uuid.uuid4()))

    def delete_object(self, device_id: str, path: str) -> str:
        data = self._request("DELETE", f"/devices/{device_id}/objects/{path}")
        return data.get("task_id", str(uuid.uuid4()))

    def reboot(self, device_id: str) -> str:
        data = self._request("POST", f"/devices/{device_id}/reboot")
        return data.get("task_id", str(uuid.uuid4()))

    def factory_reset(self, device_id: str) -> str:
        data = self._request("POST", f"/devices/{device_id}/factory-reset")
        return data.get("task_id", str(uuid.uuid4()))

    def download_file(self, device_id: str, url: str, file_type: str) -> str:
        data = self._request("POST", f"/devices/{device_id}/download", json={"url": url, "file_type": file_type})
        return data.get("task_id", str(uuid.uuid4()))

    def create_task(self, device_id: str, name: str, *args, **kwargs) -> dict:
        data = self._request("POST", f"/devices/{device_id}/tasks", json={"name": name, "args": args, **kwargs})
        return {"task_id": data.get("task_id", str(uuid.uuid4())), "state": "CREATED", "device_id": device_id}

    def get_task(self, task_id: str) -> dict:
        return self._request("GET", f"/tasks/{task_id}")

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/tasks/{task_id}")

    def trigger_connection_request(self, device_id: str, *, url: str | None = None) -> str:
        params = {"url": url} if url else None
        data = self._request("POST", f"/devices/{device_id}/connection_request", params=params)
        return data.get("outcome", "REQUESTED")

    def manage_tags(self, device_id: str, tags: list[str]) -> None:
        self._request("POST", f"/devices/{device_id}/tags", json={"tags": tags})

    def manage_presets(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        if delete:
            self._request("DELETE", f"/presets/{name}")
        else:
            self._request("POST", "/presets", json={"name": name, ** (config or {})})

    def manage_provisions(self, name: str, *, script: str | None = None, delete: bool = False) -> None:
        if delete:
            self._request("DELETE", f"/provisions/{name}")
        else:
            self._request("POST", "/provisions", json={"name": name, "script": script})

    def manage_virtual_parameters(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        if delete:
            self._request("DELETE", f"/virtual-parameters/{name}")
        else:
            self._request("POST", "/virtual-parameters", json={"name": name, ** (config or {})})

    def upload_file(self, name: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        response = self._client.post("/files", files={"file": (name, data, content_type)})
        self._record(response.status_code < 500)
        if response.status_code >= 400:
            raise NonRetryableAdapterError(redact_log_line(response.text[:500]), code="acs_upload_failed")
        return name

    def delete_file(self, name: str) -> None:
        self._client.delete(f"/files/{name}")


class FakeACSClient(ACSClient):
    """Deterministic in-memory ACS for tests and development. Device records,
    parameter trees and task states are mutable via helpers so tests can drive
    the full task lifecycle (queued → completed / faulted, offline devices, etc.)."""

    name = "fake_acs"

    _state = None

    def __init__(self, config: dict | None = None):
        super().__init__(config)

    @classmethod
    def reset(cls) -> None:
        cls._state = {
            "devices": {},          # acs_device_id -> device record
            "tasks": {},            # task_id -> task dict
            "files": {},            # name -> (data, content_type)
            "presets": {},
            "provisions": {},
            "virtual_parameters": {},
            "fail": None,           # set to a code to make calls fail
            "connection_request_outcome": "ACCEPTED",
            "calls": [],
        }

    def _s(self) -> dict:
        if FakeACSClient._state is None:
            FakeACSClient.reset()
        return FakeACSClient._state

    def seed_device(self, *, device_id: str | None = None, oui: str = "A4B1C1", product_class: str = "ONT",
                    serial_number: str = "SN0001", parameters: dict | None = None,
                    connection_request_url: str | None = None, tags: list | None = None) -> str:
        state = self._s()
        device_id = device_id or f"dev-{uuid.uuid4().hex[:8]}"
        state["devices"][device_id] = {
            "_id": device_id, "oui": oui, "productClass": product_class, "serialNumber": serial_number,
            "parameters": dict(parameters or {}), "connection_request_url": connection_request_url,
            "tags": list(tags or []), "tasks": [],
        }
        return device_id

    def set_device_parameters(self, device_id: str, parameters: dict) -> None:
        self._s()["devices"][device_id]["parameters"].update(parameters)

    def fail_next(self, code: str) -> None:
        self._s()["fail"] = code

    def _consume_fail(self) -> str | None:
        """Return the single-use failure flag (if set) and clear it so the next
        call succeeds — fail_next is one-shot."""
        state = self._s()
        fail = state.get("fail")
        if fail:
            state["fail"] = None
        return fail

    def set_connection_request_outcome(self, outcome: str) -> None:
        self._s()["connection_request_outcome"] = outcome

    def complete_task(self, task_id: str, *, state: str = "COMPLETED", result: dict | None = None) -> None:
        task = self._s()["tasks"].get(task_id)
        if task is not None:
            task["state"] = state
            if result:
                task["result"] = result

    # -- interface -----------------------------------------------------------
    def health_check(self) -> dict:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        return {"ok": True, "version": "fake", "detail": {}}

    def search_devices(self, *, query: str = "", limit: int = 100) -> list[dict]:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        state["calls"].append(("search_devices", query))
        result = []
        for device in state["devices"].values():
            blob = f"{device['oui']} {device['productClass']} {device['serialNumber']} {' '.join(device['tags'])}"
            if not query or query.lower() in blob.lower():
                result.append({"_id": device["_id"], "oui": device["oui"],
                               "productClass": device["productClass"], "serialNumber": device["serialNumber"],
                               "tags": device["tags"]})
            if len(result) >= limit:
                break
        return result

    def get_device(self, device_id: str) -> dict | None:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        device = state["devices"].get(device_id)
        if device is None:
            return None
        return {"_id": device["_id"], "oui": device["oui"], "productClass": device["productClass"],
                "serialNumber": device["serialNumber"], "tags": device["tags"],
                "connection_request_url": device.get("connection_request_url")}

    def get_parameters(self, device_id: str, paths: list[str]) -> dict:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        device = state["devices"].get(device_id)
        if device is None:
            raise NonRetryableAdapterError("device not found", code="not_found")
        return {path: device["parameters"].get(path) for path in paths}

    def refresh_object(self, device_id: str, path: str) -> str:
        task = self._make_task(device_id, "refresh", {"path": path})
        return task["_id"]

    def set_parameters(self, device_id: str, parameters: dict) -> str:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        task = self._make_task(device_id, "setParameterValues", {"parameters": parameters})
        return task["_id"]

    def add_object(self, device_id: str, path: str) -> str:
        return self._make_task(device_id, "addObject", {"path": path})["_id"]

    def delete_object(self, device_id: str, path: str) -> str:
        return self._make_task(device_id, "deleteObject", {"path": path})["_id"]

    def reboot(self, device_id: str) -> str:
        return self._make_task(device_id, "reboot", {})["_id"]

    def factory_reset(self, device_id: str) -> str:
        return self._make_task(device_id, "factoryReset", {})["_id"]

    def download_file(self, device_id: str, url: str, file_type: str) -> str:
        return self._make_task(device_id, "download", {"url": url, "file_type": file_type})["_id"]

    def create_task(self, device_id: str, name: str, *args, **kwargs) -> dict:
        task = self._make_task(device_id, name, {"args": list(args), **kwargs})
        return {"task_id": task["_id"], "state": task["state"], "device_id": device_id}

    def get_task(self, task_id: str) -> dict:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        task = state["tasks"].get(task_id)
        if task is None:
            raise NonRetryableAdapterError("task not found", code="not_found")
        return dict(task)

    def delete_task(self, task_id: str) -> None:
        self._s()["tasks"].pop(task_id, None)

    def trigger_connection_request(self, device_id: str, *, url: str | None = None) -> str:
        fail = self._consume_fail()
        if fail:
            raise RetryableAdapterError(fail, code="acs_unreachable")
        state = self._s()
        return state["connection_request_outcome"]

    def manage_tags(self, device_id: str, tags: list[str]) -> None:
        device = self._s()["devices"].get(device_id)
        if device is not None:
            device["tags"] = list(tags)

    def manage_presets(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        state = self._s()
        if delete:
            state["presets"].pop(name, None)
        else:
            state["presets"][name] = config or {}

    def manage_provisions(self, name: str, *, script: str | None = None, delete: bool = False) -> None:
        state = self._s()
        if delete:
            state["provisions"].pop(name, None)
        else:
            state["provisions"][name] = script

    def manage_virtual_parameters(self, name: str, *, config: dict | None = None, delete: bool = False) -> None:
        state = self._s()
        if delete:
            state["virtual_parameters"].pop(name, None)
        else:
            state["virtual_parameters"][name] = config or {}

    def upload_file(self, name: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self._s()["files"][name] = (data, content_type)
        return name

    def delete_file(self, name: str) -> None:
        self._s()["files"].pop(name, None)

    # -- helpers -------------------------------------------------------------
    def _make_task(self, device_id: str, name: str, params: dict) -> dict:
        state = self._s()
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        task = {"_id": task_id, "device_id": device_id, "name": name, "params": params,
                "state": "QUEUED", "result": {}}
        state["tasks"][task_id] = task
        if device_id in state["devices"]:
            state["devices"][device_id]["tasks"].append(task_id)
        return task


def get_acs_client(config: dict | None = None) -> ACSClient:
    from os import getenv

    provider = (config or {}).get("provider") or getenv("ACS_PROVIDER", "fake")
    if provider == "genieacs":
        return GenieACSClient(config)
    return FakeACSClient(config)
