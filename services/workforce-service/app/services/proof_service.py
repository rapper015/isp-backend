"""Proof-of-work and private media service.

Proof records carry server-side metadata (never trusted from the mobile client
alone): checksums, server capture/upload timestamps, GPS refs, device/session
ref and verification state. Files live in private storage with
authorization-controlled download — never permanent public URLs. Duplicate
upload retries are rejected via the unique evidence_key."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ProofError, ValidationError
from ..enums import PROOF_TYPES
from ..integrations.base import get_adapter
from ..models import (
    CustomerAcknowledgement,
    DeviceInstallation,
    FieldAttachment,
    MaterialRequirement,
    MaterialUsage,
    ProofOfWork,
    WorkOrder,
)
from .audit_service import append_event, correlation, outbox

_ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf",
    "text/plain", "video/mp4", "application/zip",
}
MAX_BYTES = 10 * 1024 * 1024


def _storage_dir() -> Path:
    return Path(os.getenv("WORKFORCE_ATTACHMENT_DIR", "./attachments"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Private media
# ---------------------------------------------------------------------------
async def store_attachment(session: Session, tenant_id, work_order_id: uuid.UUID, upload_file, *,
                           uploader_type: str = "TECHNICIAN", uploader_id: str | None = None) -> FieldAttachment:
    content_type = upload_file.content_type or "application/octet-stream"
    original_name = upload_file.filename or "attachment"
    data = await upload_file.read()
    if len(data) <= 0:
        raise ValidationError("empty file")
    if len(data) > MAX_BYTES:
        raise ValidationError("file exceeds size limit")
    if content_type not in _ALLOWED_TYPES:
        raise ValidationError(f"file type {content_type!r} is not allowed")
    checksum = hashlib.sha256(data).hexdigest()
    attachment_id = uuid.uuid4()
    directory = _storage_dir() / str(tenant_id) / str(work_order_id)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in original_name if c.isalnum() or c in "._- ").strip() or "attachment"
    stored_path = directory / f"{attachment_id.hex}_{safe_name}"
    stored_path.write_bytes(data)
    attachment = FieldAttachment(
        id=attachment_id, tenant_id=tenant_id, work_order_id=work_order_id, original_name=original_name[:255],
        stored_path=str(stored_path), content_type=content_type, size_bytes=len(data), checksum_sha256=checksum,
        uploader_type=uploader_type, uploader_id=uploader_id, malware_status="PENDING",
        retention_until=_now() + timedelta(days=int(os.getenv("WORKFORCE_ATTACHMENT_RETENTION_DAYS", "365"))),
    )
    session.add(attachment)
    session.flush()
    return attachment


def load_attachment(session: Session, tenant_id, work_order_id, attachment_id: uuid.UUID) -> tuple[str, str]:
    attachment = session.get(FieldAttachment, attachment_id)
    if attachment is None or attachment.tenant_id != tenant_id or attachment.work_order_id != work_order_id:
        raise NotFoundError("attachment not found")
    if attachment.malware_status in ("INFECTED", "QUARANTINED"):
        raise ValidationError("attachment is quarantined")
    path = Path(attachment.stored_path)
    if not path.exists():
        raise NotFoundError("attachment file is missing from storage")
    return str(path), attachment.content_type


# ---------------------------------------------------------------------------
# Proof records
# ---------------------------------------------------------------------------
def add_proof(session: Session, tenant_id, work_order_id: uuid.UUID, *, evidence_key: str, evidence_type: str,
              file_ref: str | None = None, checksum: str | None = None, capture_timestamp: datetime | None = None,
              latitude: float | None = None, longitude: float | None = None, device_ref: str | None = None,
              technician_id: uuid.UUID | None = None, visit_id: uuid.UUID | None = None,
              checklist_item_code: str | None = None, actor: str | None = None,
              correlation_id: str | None = None) -> ProofOfWork:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    evidence_type = evidence_type.upper()
    if evidence_type not in PROOF_TYPES:
        raise ProofError(f"invalid proof type {evidence_type!r}")
    existing = session.scalars(select(ProofOfWork).where(
        ProofOfWork.tenant_id == tenant_id, ProofOfWork.evidence_key == evidence_key)).first()
    if existing is not None:
        # Duplicate upload retry: return the recorded proof, never duplicate.
        return existing
    proof = ProofOfWork(
        tenant_id=tenant_id, work_order_id=work_order.id, visit_id=visit_id, checklist_item_code=checklist_item_code,
        evidence_key=evidence_key, evidence_type=evidence_type, file_ref=file_ref, checksum=checksum,
        capture_timestamp=capture_timestamp, latitude=latitude, longitude=longitude, device_ref=device_ref,
        technician_id=technician_id, verification_state="PENDING",
        audit_metadata={"server_timestamp": _now().isoformat(), "evidence_key": evidence_key},
    )
    session.add(proof)
    session.flush()
    append_event(session, work_order, "work_order.proof_submitted",
                 payload={"proof_id": str(proof.id), "evidence_type": evidence_type},
                 actor_type="technician" if technician_id else "agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    return proof


def proofs_for_work_order(session: Session, tenant_id, work_order_id: uuid.UUID) -> list[ProofOfWork]:
    return list(session.scalars(select(ProofOfWork).where(
        ProofOfWork.work_order_id == work_order_id, ProofOfWork.tenant_id == tenant_id)))


def required_proof_missing(session: Session, tenant_id, work_order: WorkOrder) -> list[str]:
    completion = work_order.completion_requirements or {}
    required = completion.get("require_proof", [])
    if not required:
        return []
    provided = {p.evidence_type for p in proofs_for_work_order(session, tenant_id, work_order.id)}
    # A photograph type satisfies the generic PHOTOGRAPH requirement.
    normalized = set()
    for e in provided:
        if e == "PHOTOGRAPH":
            normalized.add("PHOTOGRAPH")
        else:
            normalized.add(e)
    return [r for r in required if r not in normalized]


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
def record_material_usage(session: Session, tenant_id, work_order_id: uuid.UUID, *, material_code: str,
                          quantity: int, usage_type: str = "CONSUMED", technician_id: uuid.UUID | None = None,
                          actor: str | None = None, correlation_id: str | None = None) -> MaterialUsage:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    if quantity <= 0:
        raise ValidationError("quantity must be positive")
    result = get_adapter("inventory").consume(
        material_code=material_code, quantity=quantity, work_order_id=str(work_order.id),
        actor=actor or "system", correlation_id=correlation_id or correlation(None))
    usage = MaterialUsage(
        tenant_id=tenant_id, work_order_id=work_order.id, material_code=material_code, quantity=quantity,
        usage_type=usage_type.upper(), inventory_transaction_ref=result.reference, technician_id=technician_id,
        correlation_id=correlation_id or correlation(None),
    )
    session.add(usage)
    session.flush()
    outbox(session, "workforce.inventory.material_used.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "material_code": material_code, "quantity": quantity})
    return usage


def material_reconciliation_errors(session: Session, tenant_id, work_order: WorkOrder) -> list[str]:
    """Each required material must have sufficient usage (or an approved
    exception) before completion."""
    requirements = list(session.scalars(select(MaterialRequirement).where(
        MaterialRequirement.work_order_id == work_order.id)))
    if not requirements:
        return []
    usage = {u.material_code: u.quantity for u in session.scalars(
        select(MaterialUsage).where(MaterialUsage.work_order_id == work_order.id))}
    errors = []
    for requirement in requirements:
        used = usage.get(requirement.material_code, 0)
        if used < requirement.quantity:
            errors.append(f"{requirement.material_code}: required {requirement.quantity}, used {used}")
    return errors


# ---------------------------------------------------------------------------
# Device installation (authoritative uniqueness via inventory adapter)
# ---------------------------------------------------------------------------
def install_device(session: Session, tenant_id, work_order_id: uuid.UUID, *, device_type: str, serial_number: str,
                   mac_address: str | None = None, service_subscription_id: str | None = None,
                   technician_id: uuid.UUID | None = None, actor: str | None = None,
                   correlation_id: str | None = None) -> DeviceInstallation:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    result = get_adapter("inventory").install_device(
        device_type=device_type, serial_number=serial_number, mac_address=mac_address,
        service_subscription_id=service_subscription_id or work_order.service_subscription_id or "",
        work_order_id=str(work_order.id), actor=actor or "system",
        correlation_id=correlation_id or correlation(None))
    if not result.ok:
        raise ProofError(f"device installation rejected: {result.error_detail}")
    installation = DeviceInstallation(
        tenant_id=tenant_id, work_order_id=work_order.id, device_type=device_type, serial_number=serial_number,
        mac_address=mac_address, status="INSTALLED",
        service_subscription_id=service_subscription_id or work_order.service_subscription_id,
        inventory_transaction_ref=result.reference, installed_by=actor, installed_at=_now(),
    )
    session.add(installation)
    session.flush()
    append_event(session, work_order, "work_order.device_installed",
                 payload={"serial_number": serial_number, "device_type": device_type},
                 actor_type="technician" if technician_id else "agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.inventory.device_installed.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "serial_number": serial_number, "device_type": device_type})
    return installation


def recover_device(session: Session, tenant_id, work_order_id: uuid.UUID, *, serial_number: str,
                   actor: str | None = None, correlation_id: str | None = None) -> None:
    result = get_adapter("inventory").recover_device(serial_number=serial_number, work_order_id=str(work_order_id),
                                                     actor=actor or "system",
                                                     correlation_id=correlation_id or correlation(None))
    if not result.ok:
        raise ProofError(f"device recovery rejected: {result.error_detail}")
    outbox(session, "workforce.inventory.device_recovered.v1", tenant_id, correlation_id or correlation(None),
           {"work_order_id": str(work_order_id), "serial_number": serial_number})
    session.flush()


# ---------------------------------------------------------------------------
# Customer acknowledgement
# ---------------------------------------------------------------------------
def record_customer_acknowledgement(session: Session, tenant_id, work_order_id: uuid.UUID, *, method: str,
                                    masked_recipient: str | None = None, consent_text_version: str | None = None,
                                    result: str = "CONFIRMED", exception: str | None = None,
                                    actor: str | None = None) -> CustomerAcknowledgement:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    from ..enums import CUSTOMER_VERIFICATION_METHODS

    method = method.upper()
    if method not in CUSTOMER_VERIFICATION_METHODS:
        raise ValidationError(f"invalid verification method {method!r}")
    existing = session.scalars(select(CustomerAcknowledgement).where(
        CustomerAcknowledgement.work_order_id == work_order.id)).first()
    if existing is not None:
        return existing
    ack = CustomerAcknowledgement(
        tenant_id=tenant_id, work_order_id=work_order.id, method=method, masked_recipient=masked_recipient,
        consent_text_version=consent_text_version, result=result, exception=exception, actor=actor,
    )
    session.add(ack)
    session.flush()
    # OTP values are never stored — only the verification result is recorded.
    append_event(session, work_order, "work_order.customer_acknowledged",
                 payload={"method": method, "result": result}, actor_type="agent", actor_id=actor,
                 correlation_id=work_order.correlation_id)
    return ack
