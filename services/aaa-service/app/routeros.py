"""Safe, replaceable RouterOS operations; no arbitrary command endpoint exists."""
from abc import ABC, abstractmethod
import ipaddress
from os import getenv

class RouterOSAdapter(ABC):
    @abstractmethod
    def test_connection(self) -> dict: ...
    @abstractmethod
    def discover_radius_aaa(self) -> dict: ...
    @abstractmethod
    def apply_managed_radius(self, desired: dict) -> dict: ...
    @abstractmethod
    def verify_configuration(self, desired: dict) -> dict: ...

def validate_management_address(host: str) -> str:
    """Permit IP management endpoints only in administrator-approved networks."""
    try: address = ipaddress.ip_address(host.strip())
    except ValueError: raise ValueError("management host must be an IP address")
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise ValueError("management address is not allowed")
    approved = [item.strip() for item in getenv("NAS_APPROVED_NETWORKS", "").split(",") if item.strip()]
    if not approved: raise ValueError("no approved NAS management networks configured")
    try:
        if not any(address in ipaddress.ip_network(network, strict=False) for network in approved): raise ValueError("management address is outside approved networks")
    except ValueError as error:
        if str(error).startswith("management"): raise
        raise ValueError("invalid approved NAS management network") from error
    return str(address)
