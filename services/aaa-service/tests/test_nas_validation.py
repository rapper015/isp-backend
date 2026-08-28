"""Unit tests: address validation, SSRF protection, ports, normalization."""
import pytest

from app.routeros import (format_interval, normalize_radius_entry, normalize_radius_incoming, parse_interval, parse_routeros_version, redact, validate_management_address, validate_management_hostname, validate_port, validate_radius_source_ip)


def test_management_address_requires_approved_network(monkeypatch):
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.0.0.0/8")
    assert validate_management_address("10.1.2.3") == "10.1.2.3"
    with pytest.raises(ValueError):
        validate_management_address("11.0.0.1")
    with pytest.raises(ValueError):
        validate_management_address("not-an-ip")


def test_management_address_ssrf_protection(monkeypatch):
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "0.0.0.0/0")
    with pytest.raises(ValueError):
        validate_management_address("127.0.0.1")      # loopback
    with pytest.raises(ValueError):
        validate_management_address("169.254.169.254")  # link-local / metadata
    with pytest.raises(ValueError):
        validate_management_address("::1")            # loopback v6
    with pytest.raises(ValueError):
        validate_management_address("0.0.0.0")        # unspecified
    with pytest.raises(ValueError):
        validate_management_address("255.255.255.255")  # broadcast/multicast


def test_management_hostname_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("NAS_ALLOW_HOSTNAMES", "false")
    with pytest.raises(ValueError):
        validate_management_hostname("router.example.com")
    monkeypatch.setenv("NAS_ALLOW_HOSTNAMES", "true")
    assert validate_management_hostname("router.example.com") == "router.example.com"
    with pytest.raises(ValueError):
        validate_management_hostname("router.internal")
    with pytest.raises(ValueError):
        validate_management_hostname("bad host name")


def test_radius_source_ip_rejects_special_addresses():
    with pytest.raises(ValueError):
        validate_radius_source_ip("127.0.0.1")
    with pytest.raises(ValueError):
        validate_radius_source_ip("0.0.0.0")
    with pytest.raises(ValueError):
        validate_radius_source_ip("224.0.0.1")
    assert validate_radius_source_ip("10.30.0.1") == "10.30.0.1"


def test_port_validation():
    assert validate_port(8729, 8729) == 8729
    with pytest.raises(ValueError):
        validate_port(0, 8729)
    with pytest.raises(ValueError):
        validate_port(70000, 8729)
    with pytest.raises(ValueError):
        validate_port("high", 8729)


def test_version_parsing():
    assert parse_routeros_version("6.49.10") == (6, 49, 10)
    assert parse_routeros_version("7.15") == (7, 15, 0)
    assert parse_routeros_version("7.15.2 (stable)") == (7, 15, 2)
    assert parse_routeros_version("garbage") is None


def test_interval_parsing_and_formatting():
    assert parse_interval("10s") == 10
    assert parse_interval("1m") == 60
    assert parse_interval("1h") == 3600
    assert parse_interval("1d") == 86400
    assert parse_interval("garbage") is None
    assert format_interval(600) == "10m"
    assert format_interval(60) == "1m"
    assert format_interval(30) == "30s"
    assert format_interval(None) is None


def test_redaction_never_leaks_secrets():
    value = {"secret": "supersecret", "nested": {"shared_secret": "other"}, "password": "pw", "ok": "visible"}
    redacted = redact(value)
    assert "supersecret" not in str(redacted)
    assert "other" not in str(redacted)
    assert "pw" not in str(redacted)
    assert redacted["ok"] == "visible"


def test_radius_entry_normalization():
    raw = {".id": "*1", "address": "10.0.0.10", "service": "pppoe,hotspot", "src-address": "10.30.0.1", "authentication-port": "1812", "accounting-port": "1813", "timeout": "3000", "disabled": "false"}
    normalized = normalize_radius_entry(raw)
    assert normalized["remote_id"] == "*1"
    assert normalized["service"] == ["pppoe", "hotspot"]
    assert normalized["src_address"] == "10.30.0.1"
    assert normalized["disabled"] is False
    assert normalized["authentication_port"] == 1812


def test_radius_incoming_normalization():
    raw = {"accept": "true", "port": "3799", "disabled": "false"}
    normalized = normalize_radius_incoming(raw)
    assert normalized["accept"] is True
    assert normalized["port"] == 3799
