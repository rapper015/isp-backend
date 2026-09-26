"""AAA integration adapter (subscriber access profile lifecycle)."""
from __future__ import annotations

import itertools
from os import getenv
import secrets

import httpx

from .base import Adapter, AdapterError, ok_result, register

_COUNTER = itertools.count(1)
_CREATED: set[str] = set()


@register
class AaaClient(Adapter):
    name = "aaa"

    def create_subscriber_profile(self, tenant_id, username, plan_reference, subscription_code) -> dict:
        index = next(_COUNTER)
        aaa_ref = f"aaa-{index:06d}"
        _CREATED.add(aaa_ref)
        return {
            "aaa_subscriber_reference": aaa_ref,
            "username": username,
            "status": "ACTIVE",
            "plan_reference": plan_reference,
        }

    def disable_subscriber(self, tenant_id, aaa_subscriber_reference) -> dict:
        return ok_result({"aaa_subscriber_reference": aaa_subscriber_reference, "disabled": True})

    def enable_subscriber(self, tenant_id, aaa_subscriber_reference) -> dict:
        return ok_result({"aaa_subscriber_reference": aaa_subscriber_reference, "enabled": True})

    def delete_subscriber(self, tenant_id, aaa_subscriber_reference) -> dict:
        _CREATED.discard(aaa_subscriber_reference)
        return ok_result({"aaa_subscriber_reference": aaa_subscriber_reference, "deleted": True})

    def update_plan(self, tenant_id, aaa_subscriber_reference, plan_reference) -> dict:
        return ok_result({"aaa_subscriber_reference": aaa_subscriber_reference, "plan_reference": plan_reference, "updated": True})

    def assign_network_policy(self, tenant_id, subscriber_id, policy_version_id, actor="oss-provisioning") -> dict:
        """Attach the version-pinned BSS policy to the OSS subscription in AAA.

        The live call is deliberately independent of the legacy profile stub.
        It uses the OSS subscription UUID as AAA's subscriber identity, so the
        policy assignment remains stable even if an access username changes.
        """
        if getenv("OSS_AAA_POLICY_ASSIGNMENT_MODE", "fake").lower() == "fake":
            return ok_result({"subscriber_id": str(subscriber_id), "policy_version_id": str(policy_version_id), "source": "oss", "mode": "test-double"})
        base_url = getenv("OSS_AAA_BASE_URL", "http://aaa-service:8000").rstrip("/")
        service_key = getenv("OSS_AAA_INTERNAL_API_KEY", "")
        if not service_key:
            raise AdapterError("OSS to AAA service authentication is not configured")
        try:
            response = httpx.post(
                f"{base_url}/api/aaa/subscribers/{subscriber_id}/policy-assignment",
                json={
                    "tenant_id": str(tenant_id),
                    "policy_version_id": str(policy_version_id),
                    "source": "oss",
                    "actor": actor,
                },
                headers={"X-AAA-Service-Key": service_key},
                timeout=5.0,
            )
        except httpx.HTTPError as error:
            raise AdapterError("AAA policy assignment is temporarily unavailable") from error
        if response.status_code == 422:
            raise AdapterError("the plan's AAA policy is no longer active")
        if response.status_code >= 400:
            raise AdapterError("AAA policy assignment is temporarily unavailable")
        return response.json()

    def issue_managed_credential(self, tenant_id, subscriber_id, username) -> dict:
        """Ask AAA to generate an initial credential without persisting it in OSS."""
        if getenv("OSS_AAA_CREDENTIAL_MODE", "fake").lower() == "fake":
            return {"credential_id": f"test-{subscriber_id}", "username": username, "password": secrets.token_urlsafe(24), "display_once": True}
        base_url = getenv("OSS_AAA_BASE_URL", "http://aaa-service:8000").rstrip("/")
        service_key = getenv("OSS_AAA_INTERNAL_API_KEY", "")
        if not service_key:
            raise AdapterError("OSS to AAA service authentication is not configured")
        try:
            response = httpx.post(
                f"{base_url}/api/aaa/subscribers/{subscriber_id}/managed-credentials",
                json={"tenant_id": str(tenant_id), "username": username, "allowed_methods": ["pap"]},
                headers={"X-AAA-Service-Key": service_key},
                timeout=8.0,
            )
        except httpx.HTTPError as error:
            raise AdapterError("AAA credential generation is temporarily unavailable") from error
        if response.status_code == 409:
            raise AdapterError("an access credential already exists; use credential rotation instead")
        if response.status_code >= 400:
            raise AdapterError("AAA credential generation is temporarily unavailable")
        return response.json()

    def get_subscriber_usage(self, tenant_id, subscriber_id) -> dict:
        """Read the current FUP cycle through AAA's private service boundary."""
        base_url = getenv("OSS_AAA_BASE_URL", "http://aaa-service:8000").rstrip("/")
        service_key = getenv("OSS_AAA_INTERNAL_API_KEY", "")
        if not service_key:
            raise AdapterError("OSS to AAA service authentication is not configured")
        try:
            response = httpx.get(
                f"{base_url}/api/aaa/fup/subscribers/{subscriber_id}/usage",
                params={"tenant_id": str(tenant_id)},
                headers={"X-AAA-Service-Key": service_key},
                timeout=5.0,
            )
        except httpx.HTTPError as error:
            raise AdapterError("usage information is temporarily unavailable") from error
        if response.status_code == 404:
            return {"subscriber_id": str(subscriber_id), "available": False, "input_octets": 0, "output_octets": 0, "active_tier": None}
        if response.status_code >= 400:
            raise AdapterError("usage information is temporarily unavailable")
        return {"available": True, **response.json()}
