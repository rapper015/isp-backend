"""IPAM integration adapter (IP allocation/release)."""
from __future__ import annotations

import itertools

from .base import Adapter, ok_result, register

_COUNTER = itertools.count(1)


@register
class IpamClient(Adapter):
    name = "ipam"

    def allocate_ip(self, tenant_id, order_id, pool_code="default-pool") -> dict:
        index = next(_COUNTER)
        return {
            "address": f"10.{index % 250 + 1}.{(index // 250) % 250 + 1}.{index % 250 + 2}",
            "pool_code": pool_code,
            "allocation_ref": f"ipam-{index:06d}",
        }

    def release_ip(self, tenant_id, address, allocation_ref=None) -> dict:
        return ok_result({"address": address, "released": True})

    def find_pool(self, tenant_id, service_location_id) -> str:
        return "default-pool"
