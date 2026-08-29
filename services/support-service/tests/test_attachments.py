"""Attachment tests: private storage, authorization-controlled download, size
and type validation, and tenant isolation."""
import asyncio

import pytest

from app.domain.exceptions import NotFoundError, ValidationError
from app.models import Tenant
from app.services import attachment_service


class _FakeUpload:
    def __init__(self, data: bytes, content_type: str, filename: str):
        self.data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self):
        return self.data


async def _store(session, tenant_id, ticket, **kw):
    upload = _FakeUpload(kw.pop("data", b"\xff\xd8\xff\xe0jpegdata"), kw.pop("content_type", "image/jpeg"),
                         kw.pop("filename", "photo.jpg"))
    return await attachment_service.store_attachment(session, tenant_id, ticket, upload,
                                                     uploader_type="AGENT", uploader_id="a1", **kw)


def test_store_and_download(session, tenant_id, make_ticket):
    ticket = make_ticket()
    attachment = asyncio.run(_store(session, tenant_id, ticket))
    session.commit()
    assert attachment.size_bytes == len(b"\xff\xd8\xff\xe0jpegdata")
    assert len(attachment.checksum_sha256) == 64
    path, content_type = attachment_service.load_attachment(session, tenant_id, ticket.id, attachment.id)
    assert content_type == "image/jpeg"
    assert path.endswith("photo.jpg")


def test_rejects_oversized(session, tenant_id, make_ticket):
    ticket = make_ticket()
    big = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError):
        asyncio.run(_store(session, tenant_id, ticket, data=big, content_type="application/pdf", filename="big.pdf"))


def test_rejects_disallowed_type(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(ValidationError):
        asyncio.run(_store(session, tenant_id, ticket, data=b"MZ", content_type="application/x-msdownload", filename="evil.exe"))


def test_tenant_isolation_on_download(session, tenant_id, make_ticket):
    ticket = make_ticket()
    attachment = asyncio.run(_store(session, tenant_id, ticket))
    session.commit()
    other = Tenant(name="Other", code="OTHERX")
    session.add(other)
    session.commit()
    session.refresh(other)
    with pytest.raises(NotFoundError):
        attachment_service.load_attachment(session, other.id, ticket.id, attachment.id)


def test_wrong_ticket_denied(session, tenant_id, make_ticket):
    ticket_a = make_ticket()
    ticket_b = make_ticket()
    attachment = asyncio.run(_store(session, tenant_id, ticket_a))
    session.commit()
    with pytest.raises(NotFoundError):
        attachment_service.load_attachment(session, tenant_id, ticket_b.id, attachment.id)
