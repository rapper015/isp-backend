from django.db import transaction
from django.utils import timezone

from aaa.exceptions import AppError

from .models import IPAddress, IPPool, VlanPool


def allocate_ip_address(pool_code: str, order, subscriber=None) -> IPAddress:
    pool = IPPool.objects.filter(pool_code=pool_code, deleted_at__isnull=True).first()
    if pool is None:
        raise AppError(f"IP pool '{pool_code}' not found", 404)

    with transaction.atomic():
        ip_address = (
            IPAddress.objects.select_for_update()
            .filter(pool=pool, status=IPAddress.Status.AVAILABLE)
            .order_by("id")
            .first()
        )
        if ip_address is None:
            raise AppError(f"No available IP addresses in pool '{pool_code}'", 409)

        ip_address.status = IPAddress.Status.ALLOCATED
        ip_address.allocated_order = order
        ip_address.allocated_subscriber = subscriber
        ip_address.allocated_at = timezone.now()
        ip_address.save(
            update_fields=["status", "allocated_order", "allocated_subscriber", "allocated_at"]
        )
        return ip_address


def release_ip_address(ip_address: IPAddress) -> None:
    if ip_address.status == IPAddress.Status.AVAILABLE:
        return
    ip_address.status = IPAddress.Status.AVAILABLE
    ip_address.allocated_order = None
    ip_address.allocated_subscriber = None
    ip_address.allocated_at = None
    ip_address.save(
        update_fields=["status", "allocated_order", "allocated_subscriber", "allocated_at"]
    )


def get_active_vlan(vlan_id) -> VlanPool:
    """Looks up by the VLAN tag number (Plan.vlan), not the pool's primary key."""
    vlan = VlanPool.objects.filter(
        vlan_id=vlan_id, deleted_at__isnull=True, status=VlanPool.Status.ACTIVE
    ).first()
    if vlan is None:
        raise AppError(f"VLAN '{vlan_id}' not found or inactive", 404)
    return vlan
