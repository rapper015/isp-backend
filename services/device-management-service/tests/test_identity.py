"""Device identity normalization, tenant-resolution classification and
connection-request SSRF protection."""
import pytest

from app.domain import identity as identity_rules
from app.domain import ssrf
from app.domain.exceptions import SSRFProtectionError


def test_normalize_oui():
    assert identity_rules.normalize_oui("A4-B1-C1") == "A4B1C1"
    assert identity_rules.normalize_oui("a4b1c1") == "A4B1C1"
    assert identity_rules.normalize_oui("") == ""


def test_normalize_mac():
    assert identity_rules.normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert identity_rules.normalize_mac("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"
    assert identity_rules.normalize_mac("not-a-mac") is None


def test_acs_identity_key_never_uses_mac():
    oui, product, serial = identity_rules.acs_identity_key("a4b1c1", "AN5506", "SN1")
    assert oui == "A4B1C1"
    assert product == "AN5506"
    assert serial == "SN1"


def test_resolve_outcome_classification():
    assert identity_rules.resolve_outcome(1.0, matched=True, conflicting=False) == "MATCHED"
    assert identity_rules.resolve_outcome(0.5, matched=True, conflicting=False) == "AMBIGUOUS"
    assert identity_rules.resolve_outcome(1.0, matched=True, conflicting=True) == "AMBIGUOUS"
    assert identity_rules.resolve_outcome(None, matched=False, conflicting=False) == "UNKNOWN"
    assert identity_rules.resolve_outcome(None, matched=True, conflicting=False, blocked=True) == "BLOCKED"


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------
def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("ftp://10.0.0.5:2121/")


def test_ssrf_rejects_private_ip():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://10.0.0.5:7547/")


def test_ssrf_rejects_loopback():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://127.0.0.1:7547/")


def test_ssrf_rejects_metadata_endpoint():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://169.254.169.254/latest/meta-data/")


def test_ssrf_rejects_link_local():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://169.254.0.1:7547/")


def test_ssrf_rejects_localhost_hostname():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://localhost:7547/")


def test_ssrf_rejects_non_allowed_port():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://example.com:22/")


def test_ssrf_rejects_credentials_in_url():
    with pytest.raises(SSRFProtectionError):
        ssrf.validate_connection_request_url("http://user:pass@example.com:7547/")


def test_ssrf_allows_public_tr069_port():
    # Public host, allowed TR-069 port — DNS resolution may still fail in tests,
    # but the structural validation must pass before resolution.
    result = ssrf.validate_connection_request_url("http://cpe.example.com:7547/")
    assert result.startswith("http://")
