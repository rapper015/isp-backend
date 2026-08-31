from collections import defaultdict

from django.utils import timezone

from aaa.exceptions import AppError
from aaa.models import ActiveSession

from .models import NasDevice


def _sync_nas_devices_from_sessions() -> None:
    for session in ActiveSession.objects.all():
        service_types = ["pppoe"] if session.called_station_id else []
        # Deliberately not scoped to deleted_at__isnull=True: nas_ip_address is
        # unique, so a device coming back online must revive its existing row
        # (via deleted_at=None below) rather than collide on a fresh insert.
        NasDevice.objects.update_or_create(
            franchise=None,
            nas_ip_address=session.nas_ip_address,
            defaults={
                "nas_identifier": session.nas_identifier,
                "name": session.nas_identifier or session.nas_ip_address,
                "status": NasDevice.Status.ONLINE,
                "last_seen_at": session.last_interim_at or session.started_at,
                "service_types": service_types,
                "deleted_at": None,
            },
        )


def list_nas_devices() -> list[dict]:
    _sync_nas_devices_from_sessions()

    active_counts: dict[str, int] = defaultdict(int)
    active_subscribers: dict[str, set] = defaultdict(set)
    for nas_ip_address, username in ActiveSession.objects.values_list(
        "nas_ip_address", "username"
    ):
        active_counts[nas_ip_address] += 1
        active_subscribers[nas_ip_address].add(username)

    devices = NasDevice.objects.filter(deleted_at__isnull=True).order_by(
        "-last_seen_at", "-created_at"
    )

    results = []
    for device in devices:
        results.append(
            {
                "device": device,
                "online_session_count": active_counts.get(device.nas_ip_address, 0),
                "active_subscribers": sorted(active_subscribers.get(device.nas_ip_address, set())),
            }
        )
    return results


def delete_nas_device(device_id) -> None:
    device = NasDevice.objects.filter(id=device_id, deleted_at__isnull=True).first()
    if device is None:
        raise AppError("NAS device not found", 404)
    device.deleted_at = timezone.now()
    device.save(update_fields=["deleted_at"])
