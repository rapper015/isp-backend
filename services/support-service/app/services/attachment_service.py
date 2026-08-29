"""Private attachment storage for support tickets.

Attachments live in private object storage (a local directory by default;
swap-in object storage for production) and are only served through
authorization-controlled download endpoints — never as permanent public URLs.
Enforces size limits, MIME allow-list, checksums, tenant isolation and a
malware-scanning hook (PENDING until scanned)."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..enums import ALLOWED_ATTACHMENT_TYPES, MAX_ATTACHMENT_BYTES
from ..models import TicketAttachment


def _storage_dir() -> Path:
    return Path(os.getenv("SUPPORT_ATTACHMENT_DIR", "./attachments"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate(content_type: str, size: int, filename: str) -> None:
    if size <= 0:
        raise ValidationError("empty file")
    if size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(f"file exceeds {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB limit")
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        raise ValidationError(f"file type {content_type!r} is not allowed")
    if not filename or "/" in filename or "\\" in filename:
        raise ValidationError("invalid filename")


async def store_attachment(session: Session, tenant_id, ticket, upload_file, *, uploader_type: str,
                           uploader_id: str, visibility: str = "PUBLIC") -> TicketAttachment:
    content_type = upload_file.content_type or "application/octet-stream"
    original_name = upload_file.filename or "attachment"
    data = await upload_file.read()
    _validate(content_type, len(data), original_name)
    checksum = hashlib.sha256(data).hexdigest()

    attachment_id = uuid.uuid4()
    directory = _storage_dir() / str(tenant_id) / str(ticket.id)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in original_name if c.isalnum() or c in "._- ").strip() or "attachment"
    stored_path = directory / f"{attachment_id.hex}_{safe_name}"
    stored_path.write_bytes(data)

    attachment = TicketAttachment(
        id=attachment_id,
        tenant_id=tenant_id,
        ticket_id=ticket.id,
        original_name=original_name[:255],
        stored_path=str(stored_path),
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        visibility=visibility.upper(),
        uploader_type=uploader_type,
        uploader_id=uploader_id,
        malware_status="PENDING",  # scan hook; CLEAN when the scanner confirms
        retention_until=_now() + timedelta(days=int(os.getenv("SUPPORT_ATTACHMENT_RETENTION_DAYS", "365"))),
    )
    session.add(attachment)
    session.flush()
    return attachment


def load_attachment(session: Session, tenant_id, ticket_id, attachment_id: uuid.UUID) -> tuple[str, str]:
    attachment = session.get(TicketAttachment, attachment_id)
    if attachment is None or attachment.tenant_id != tenant_id or attachment.ticket_id != ticket_id:
        raise NotFoundError("attachment not found")
    if attachment.malware_status in ("INFECTED", "QUARANTINED"):
        raise ValidationError("attachment is quarantined")
    path = Path(attachment.stored_path)
    if not path.exists():
        raise NotFoundError("attachment file is missing from storage")
    return str(path), attachment.content_type
