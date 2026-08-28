import ipaddress
import socket

from django.conf import settings


class UnsafeRouterAddress(ValueError): pass


def validate_router_host(host):
    host=(host or "").strip()
    if not host or len(host)>253: raise UnsafeRouterAddress("INVALID_HOST")
    try: results=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)
    except socket.gaierror as exc: raise UnsafeRouterAddress("HOST_RESOLUTION_FAILED") from exc
    addresses={ipaddress.ip_address(item[4][0]) for item in results}
    allowed=[ipaddress.ip_network(item,strict=False) for item in settings.NAS_ALLOWED_NETWORKS]
    for address in addresses:
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            raise UnsafeRouterAddress("HOST_ADDRESS_BLOCKED")
        if address.is_private and not settings.NAS_ALLOW_PRIVATE_NETWORKS and not any(address in network for network in allowed):
            raise UnsafeRouterAddress("PRIVATE_ADDRESS_NOT_ALLOWED")
        if allowed and not any(address in network for network in allowed) and address.is_private:
            raise UnsafeRouterAddress("ADDRESS_OUTSIDE_ALLOWED_NETWORKS")
    return host, sorted(str(item) for item in addresses)
