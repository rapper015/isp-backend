"""AAA integration adapter (subscriber access profile lifecycle)."""
from __future__ import annotations

import itertools

from .base import Adapter, ok_result, register

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
