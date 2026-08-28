"""M3 RADIUS compiler: MikroTik rate-limit direction, bursts, IP/timeout
attributes, invalid-policy rejection, and the policy.py direction fix."""
import pytest

from app.network_control.radius_compiler import (
    compile_radius_attributes,
    format_mikrotik_rate_limit,
    validate_policy_body,
)


def test_rate_limit_direction_is_download_upload():
    """RouterOS rx/tx is from the router's perspective: the attribute MUST be
    download/upload. upload=1024, download=512 -> '512k/1024k'."""
    assert format_mikrotik_rate_limit(upload_kbps=1024, download_kbps=512) == "512k/1024k"


def test_rate_limit_direction_not_inverted():
    assert format_mikrotik_rate_limit(upload_kbps=20000, download_kbps=50000) == "50M/20M"
    assert format_mikrotik_rate_limit(upload_kbps=1, download_kbps=2) == "2k/1k"


def test_rate_limit_bursts():
    value = format_mikrotik_rate_limit(
        upload_kbps=1000,
        download_kbps=2000,
        burst_upload_kbps=2000,
        burst_download_kbps=4000,
        burst_threshold_bytes=1048576,
        burst_duration_seconds=16,
    )
    # RouterOS: rx/tx burst-rx burst-tx burst-threshold burst-time
    assert value == "2M/1M 4M 2M 1048576 16"


def test_rate_limit_requires_both_directions():
    with pytest.raises(ValueError):
        format_mikrotik_rate_limit(upload_kbps=None, download_kbps=1000)


def test_compile_static_ip_and_pool():
    policy = {
        "upload_kbps": 1024,
        "download_kbps": 2048,
        "static_ipv4": "198.51.100.7",
        "session_timeout": 3600,
        "idle_timeout": 600,
        "interim_interval": 300,
        "simultaneous_limit": 2,
        "filter_id": "pppoe-fiber",
        "mikrotik_group": "fiber-2M",
    }
    reply = compile_radius_attributes(policy)
    assert reply["Mikrotik-Rate-Limit"] == "2048k/1024k"
    assert reply["Framed-IP-Address"] == "198.51.100.7"
    assert reply["Session-Timeout"] == 3600
    assert reply["Idle-Timeout"] == 600
    assert reply["Acct-Interim-Interval"] == 300
    assert reply["Simultaneous-Use"] == 2
    assert reply["Filter-Id"] == "pppoe-fiber"
    assert reply["Mikrotik-Group"] == "fiber-2M"


def test_compile_dynamic_pool():
    reply = compile_radius_attributes({"upload_kbps": 512, "download_kbps": 1024, "ipv4_pool": "pppoe-pool-1"})
    assert reply["Mikrotik-Rate-Limit"] == "1024k/512k"
    assert reply["Framed-Pool"] == "pppoe-pool-1"


def test_compile_ipv6_and_vlan():
    reply = compile_radius_attributes({"upload_kbps": 512, "download_kbps": 512, "static_ipv6": "2001:db8::10", "ipv6_pool": "v6-pool", "vlan": 100})
    assert reply["Framed-IPv6-Prefix"] == "2001:db8::10"
    assert reply["Framed-IPv6-Pool"] == "v6-pool"
    assert reply["Tunnel-Private-Group-Id"] == 100


def test_invalid_policy_keys_rejected():
    errors = validate_policy_body({"upload_kbps": 100, "not_a_policy_key": 1})
    assert any("unknown policy key" in error for error in errors)


def test_negative_rate_rejected():
    errors = validate_policy_body({"upload_kbps": -5, "download_kbps": 100})
    assert any("upload_kbps" in error for error in errors)


def test_policy_py_effective_policy_direction_fix():
    """The legacy policy.py path must also emit download/upload ordering."""
    from app.policy import EffectivePolicy

    effective = EffectivePolicy(values={"upload_kbps": 1024, "download_kbps": 512}, provenance={})
    assert effective.reply_attributes()["Mikrotik-Rate-Limit"] == "512k/1024k"


def test_unknown_reply_attributes_are_filtered():
    reply = compile_radius_attributes({"upload_kbps": 1000, "download_kbps": 2000, "reply_attributes": {"User-Password": "leaked"}})
    assert "User-Password" not in reply
