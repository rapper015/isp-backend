"""ACS instance management: register instances, test connections, health,
version/capabilities, reconciliation and device bindings."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..integrations.acs import get_acs_client
from ..models import ACSCapability, ACSDeviceBinding, ACSHealth, ACSInstance
from .audit_service import audit, correlation


def get_instance_or_404(session: Session, instance_id: uuid.UUID) -> ACSInstance:
    instance = session.get(ACSInstance, instance_id)
    if instance is None:
        raise NotFoundError("ACS instance not found")
    return instance


def register_instance(session: Session, *, name: str, base_url: str, tenant_id: uuid.UUID | None = None,
                      environment: str = "PRODUCTION", cwmp_endpoint: str | None = None,
                      file_service_endpoint: str | None = None, actor: str = "system") -> ACSInstance:
    if not base_url.startswith(("http://", "https://")):
        raise ValidationError("base_url must be an http(s) URL")
    existing = session.scalars(select(ACSInstance).where(
        ACSInstance.tenant_id == tenant_id, ACSInstance.name == name)).first()
    if existing is not None:
        raise ValidationError(f"ACS instance {name!r} already exists")
    instance = ACSInstance(tenant_id=tenant_id, name=name, base_url=base_url, environment=environment,
                           cwmp_endpoint=cwmp_endpoint, file_service_endpoint=file_service_endpoint,
                           health="UNKNOWN", is_active=True)
    session.add(instance)
    session.flush()
    audit(session, tenant_id, "acs.instance.registered", "acs_instance", str(instance.id), actor=actor,
          correlation_id=correlation(None), payload={"name": name, "environment": environment})
    return instance


def health_check(session: Session, instance_id: uuid.UUID, *, actor: str = "system") -> ACSHealth:
    from datetime import datetime, timezone

    instance = get_instance_or_404(session, instance_id)
    client = get_acs_client({"base_url": instance.base_url})
    record = ACSHealth(instance_id=instance.id, state="UNKNOWN", version=None)
    try:
        result = client.health_check()
        record.state = "HEALTHY"
        record.version = result.get("version")
        instance.version = result.get("version")
        instance.health = "HEALTHY"
        instance.last_health_check_at = datetime.now(timezone.utc)
    except Exception as error:  # noqa: BLE001
        record.state = "UNREACHABLE"
        record.detail = {"error": str(error)}
        instance.health = "UNREACHABLE"
    session.add(record)
    session.flush()
    return record


def capture_capabilities(session: Session, instance_id: uuid.UUID) -> list[ACSCapability]:
    instance = get_instance_or_404(session, instance_id)
    client = get_acs_client({"base_url": instance.base_url})
    rows = []
    for name, supported in ({"presets": True, "provisions": True, "virtual_parameters": True,
                             "files": True, "tasks": True}).items():
        row = session.scalars(select(ACSCapability).where(
            ACSCapability.instance_id == instance.id, ACSCapability.name == name)).first()
        if row is None:
            row = ACSCapability(instance_id=instance.id, name=name, supported=supported)
            session.add(row)
        row.supported = supported
        rows.append(row)
    session.flush()
    return rows


def bind_device(session: Session, instance_id: uuid.UUID, *, cpe_id: uuid.UUID, acs_device_id: str,
                tenant_id: uuid.UUID, actor: str = "system") -> ACSDeviceBinding:
    get_instance_or_404(session, instance_id)
    binding = session.scalars(select(ACSDeviceBinding).where(
        ACSDeviceBinding.instance_id == instance_id, ACSDeviceBinding.acs_device_id == acs_device_id)).first()
    if binding is not None:
        return binding
    binding = ACSDeviceBinding(tenant_id=tenant_id, instance_id=instance_id, cpe_id=cpe_id,
                               acs_device_id=acs_device_id, created_by=actor)
    session.add(binding)
    session.flush()
    return binding


def reconcile_devices(session: Session, tenant_id, instance_id: uuid.UUID, *, actor: str = "system",
                      limit: int = 200) -> dict:
    """Incremental discovery sync: pull ACS device records and create/update
    managed CPEs (incremental sync only — full reconcile is a bounded task)."""
    from .device_service import discover_from_acs

    instance = get_instance_or_404(session, instance_id)
    client = get_acs_client({"base_url": instance.base_url})
    records = client.search_devices(limit=limit)
    created, updated, quarantined = 0, 0, 0
    for record in records:
        try:
            device = discover_from_acs(session, instance.id, acs_device_id=record["_id"],
                                       requested_tenant_id=tenant_id, actor=actor)
            if device.state in ("DISCOVERED", "QUARANTINED"):
                quarantined += 1
            else:
                updated += 1
        except Exception:  # noqa: BLE001
            quarantined += 1
    session.flush()
    return {"scanned": len(records), "created": created, "updated": updated, "quarantined": quarantined}
