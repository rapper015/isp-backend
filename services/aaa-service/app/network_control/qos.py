"""QoS compilation: vendor-neutral traffic classes / QoS profiles into typed,
platform-managed RouterOS objects (queue types, mangle rules, address lists).

All generated objects are tagged with a stable convention so platform-managed
objects are distinguishable from manual ones and are never deleted when they
belong to a human."""
from __future__ import annotations

from .enums import MANAGED_TAG, DEVICE_OBJECT_KINDS


def managed_comment(tenant_id, policy_id, version) -> str:
    return f"{MANAGED_TAG} tenant={tenant_id} policy={policy_id} version={version}"


def compile_traffic_class(tc, tenant_id, policy_id, version) -> dict:
    """Compile one TrafficClass into a mangle rule + queue type descriptor."""
    comment = managed_comment(tenant_id, policy_id, version)
    mark = tc.packet_mark or f"mark-{tc.code}"
    queue_type = {
        "kind": "queue_type",
        "name": f"qt-{tc.code}",
        "params": {"name": f"qt-{tc.code}", "kind": tc.queue_discipline, "pcq-rate": tc.mir_kbps, "pcq-classifier": "both-addresses", "comment": comment},
        "tags": {"managed": True, "tenant_id": str(tenant_id), "policy_id": str(policy_id), "version": version, "traffic_class": tc.code},
    }
    mangle_params = {"chain": "forward", "comment": comment}
    if tc.protocol:
        mangle_params["protocol"] = tc.protocol
    if tc.dscp:
        mangle_params["dscp"] = tc.dscp
    if tc.src_port:
        mangle_params["src-port"] = tc.src_port
    if tc.dst_port:
        mangle_params["dst-port"] = tc.dst_port
    mangle_params["new-packet-mark"] = mark
    mangle = {
        "kind": "mangle_rule",
        "name": f"mangle-{tc.code}",
        "params": mangle_params,
        "tags": {"managed": True, "tenant_id": str(tenant_id), "policy_id": str(policy_id), "version": version, "traffic_class": tc.code},
    }
    return queue_type, mangle


def compile_qos_profile(qos_profile, traffic_classes, tenant_id, policy_id, version) -> list[dict]:
    """Compile a QoS profile into a deterministic list of managed objects."""
    objects: list[dict] = []
    for tc in traffic_classes:
        queue_type, mangle = compile_traffic_class(tc, tenant_id, policy_id, version)
        objects.append(queue_type)
        objects.append(mangle)
    return objects


def managed_object_key(obj: dict) -> str:
    """Stable identity used for desired/observed comparison."""
    return f"{obj['kind']}:{obj.get('name') or obj.get('params', {}).get('name')}"


def is_managed(comment: str | None) -> bool:
    return bool(comment) and MANAGED_TAG in comment


def validate_managed_objects(objects: list[dict]) -> list[str]:
    """Reject any object whose kind is not allowlisted or lacks the managed tag."""
    errors: list[str] = []
    for obj in objects:
        if obj.get("kind") not in DEVICE_OBJECT_KINDS:
            errors.append(f"unsupported device object kind {obj.get('kind')!r}")
        tags = obj.get("tags", {})
        if not tags.get("managed"):
            errors.append(f"object {obj.get('name')} is not tagged as managed")
    return errors
