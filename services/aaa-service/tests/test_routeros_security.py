import pytest
from app.routeros import validate_management_address

def test_routeros_management_destination_requires_approved_network(monkeypatch):
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.50.0.0/16,2001:db8::/32")
    assert validate_management_address("10.50.1.9") == "10.50.1.9"
    assert validate_management_address("2001:db8::10") == "2001:db8::10"
    with pytest.raises(ValueError): validate_management_address("127.0.0.1")
    with pytest.raises(ValueError): validate_management_address("10.51.0.1")
