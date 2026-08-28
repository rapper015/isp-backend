"""M3 QoS compilation and managed-object tagging."""
from app.models import QosProfile, TrafficClass
from app.network_control.qos import (
    compile_qos_profile,
    is_managed,
    managed_comment,
    validate_managed_objects,
)


def _tc(tenant_id, code="voice"):
    return TrafficClass(
        tenant_id=tenant_id,
        code=code,
        name=code,
        dscp="ef",
        protocol="udp",
        dst_port="5060",
        priority=8,
        cir_kbps=512,
        mir_kbps=2048,
        packet_mark=f"mark-{code}",
        queue_discipline="pcq",
    )


def test_compile_qos_profile_objects_are_tagged(session, tenant):
    profile = QosProfile(tenant_id=tenant.id, code="premium", name="Premium QoS", tier="premium", traffic_class_ids=[], params={})
    objects = compile_qos_profile(profile, [_tc(tenant.id, "voice"), _tc(tenant.id, "video")], tenant.id, "pol-1", 2)
    assert len(objects) == 4  # queue_type + mangle per class
    for obj in objects:
        assert obj["tags"]["managed"] is True
        assert "managed-by=isp-platform" in obj["params"]["comment"]
        assert obj["tags"]["version"] == 2


def test_managed_comment_convention():
    comment = managed_comment("t1", "p1", 3)
    assert "managed-by=isp-platform" in comment
    assert "tenant=t1" in comment
    assert "policy=p1" in comment
    assert "version=3" in comment
    assert is_managed(comment)
    assert not is_managed("manual queue created by engineer")


def test_validate_managed_objects_rejects_unmanaged_or_unknown():
    objects = [{"kind": "queue_type", "name": "qt-voice", "params": {"name": "qt-voice", "comment": "managed-by=isp-platform"}, "tags": {"managed": True}}]
    assert validate_managed_objects(objects) == []
    bad_kind = [{"kind": "reboot", "name": "x", "params": {}, "tags": {"managed": True}}]
    assert validate_managed_objects(bad_kind)
    unmanaged = [{"kind": "queue_type", "name": "x", "params": {"comment": "manual"}, "tags": {"managed": False}}]
    assert validate_managed_objects(unmanaged)
