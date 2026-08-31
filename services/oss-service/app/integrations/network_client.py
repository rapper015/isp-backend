"""Network inventory integration adapter (ports, VLANs, ONT)."""
from __future__ import annotations

import itertools

from .base import Adapter, ok_result, register

_COUNTER = itertools.count(1)
_AVAILABLE_ONT = {"ONT-SN-1001", "ONT-SN-1002", "ONT-SN-1003"}
_USED_ONT: set[str] = set()


@register
class NetworkClient(Adapter):
    name = "network"

    def reserve_port(self, tenant_id, pop, node, port_type="PON_PORT") -> dict:
        index = next(_COUNTER)
        return {
            "port_reference": f"{port_type.lower()}-{pop}-{node}-{index}",
            "port_type": port_type,
            "pop": pop,
            "node": node,
        }

    def release_port(self, tenant_id, port_reference) -> dict:
        return ok_result({"port_reference": port_reference, "released": True})

    def assign_ont(self, tenant_id, ont_serial) -> dict:
        if ont_serial in _USED_ONT:
            return ok_result({"assigned": False, "reason": "ont already assigned"})
        if ont_serial not in _AVAILABLE_ONT:
            return ok_result({"assigned": False, "reason": "unknown ont"})
        _USED_ONT.add(ont_serial)
        return ok_result({"ont_serial": ont_serial, "assigned": True})

    def release_ont(self, tenant_id, ont_serial) -> dict:
        _USED_ONT.discard(ont_serial)
        return ok_result({"ont_serial": ont_serial, "released": True})
