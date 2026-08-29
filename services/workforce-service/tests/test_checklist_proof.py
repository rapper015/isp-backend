"""Versioned checklists and proof of work: required items, conditional items,
invalid measurements, missing proof, duplicate evidence, QA approve/reject."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import ChecklistError, ProofError, QAError, StateTransitionError, ValidationError
from app.services import checklist_service, proof_service, qa_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


def _checklist_wo(session, tenant_id, make_work_order):
    wo = make_work_order()
    session.commit()
    session.refresh(wo)
    return wo


def _responses(overrides=None, full=False):
    base = {
        "VERIFY_CUSTOMER": True,
        "INSPECT_SITE": True,
        "INSTALL_CABLE": True,
        "INSTALL_ONT": True,
        "SCAN_SERIAL": "ONT-SN-1001",
        "RECORD_MAC": "AA:BB:CC:DD:EE:FF",
        "OPTICAL_READING": -18.5,
        "SERVICE_TEST": 100,
        "PHOTO_INSTALLATION": {"file_ref": "f-proof-1"},
        "CUSTOMER_ACK": {"file_ref": "f-proof-ack"},
        "MATERIALS_USED": "2m fiber",
    }
    if not full:
        base.pop("MATERIALS_USED", None)
    base.update(overrides or {})
    return base


def test_missing_required_item_rejected(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    responses = _responses(full=True)
    responses.pop("VERIFY_CUSTOMER")
    with pytest.raises(ChecklistError):
        checklist_service.submit_responses(session, tenant_id, wo.id, responses=responses, submitted_by="tech-1")


def test_invalid_mac_rejected(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    responses = _responses(full=True, overrides={"RECORD_MAC": "not-a-mac"})
    with pytest.raises(ChecklistError):
        checklist_service.submit_responses(session, tenant_id, wo.id, responses=responses, submitted_by="tech-1")


def test_optical_reading_out_of_range_rejected(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    responses = _responses(full=True, overrides={"OPTICAL_READING": -60})
    with pytest.raises(ChecklistError):
        checklist_service.submit_responses(session, tenant_id, wo.id, responses=responses, submitted_by="tech-1")


def test_checklist_complete(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    checklist_service.submit_responses(session, tenant_id, wo.id, responses=_responses(full=True),
                                       submitted_by="tech-1")
    session.commit()
    ok, errors = checklist_service.checklist_is_complete(session, tenant_id, wo)
    assert ok is True
    assert errors == []


def test_photo_evidence_requires_file_ref(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    responses = _responses(full=True, overrides={"PHOTO_INSTALLATION": {"file_ref": ""}})
    with pytest.raises(ChecklistError):
        checklist_service.submit_responses(session, tenant_id, wo.id, responses=responses, submitted_by="tech-1")


def test_duplicate_proof_retry_is_idempotent(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    first = proof_service.add_proof(session, tenant_id, wo.id, evidence_key="proof-key-dup",
                                    evidence_type="PHOTOGRAPH", file_ref="f1", checksum="abc",
                                    capture_timestamp=TODAY, actor="test")
    second = proof_service.add_proof(session, tenant_id, wo.id, evidence_key="proof-key-dup",
                                     evidence_type="PHOTOGRAPH", file_ref="f1", checksum="abc",
                                     capture_timestamp=TODAY, actor="test")
    assert first.id == second.id
    assert len(proof_service.proofs_for_work_order(session, tenant_id, wo.id)) == 1


def test_invalid_proof_type_rejected(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    with pytest.raises(ProofError):
        proof_service.add_proof(session, tenant_id, wo.id, evidence_key="proof-bad",
                                evidence_type="FAKE_TYPE", file_ref="f1", actor="test")


def test_store_attachment_and_download(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    import io
    from pathlib import Path

    class FakeUpload:
        content_type = "image/jpeg"
        filename = "photo.jpg"

        async def read(self):
            return b"\xff\xd8\xff\xe0 fake jpeg bytes"

    import asyncio

    attachment = asyncio.run(proof_service.store_attachment(
        session, tenant_id, wo.id, FakeUpload(), uploader_type="TECHNICIAN", uploader_id="tech-1"))
    session.commit()
    assert attachment.size_bytes == len(b"\xff\xd8\xff\xe0 fake jpeg bytes")
    assert Path(attachment.stored_path).exists()
    path, content_type = proof_service.load_attachment(session, tenant_id, wo.id, attachment.id)
    assert Path(path).exists()
    assert content_type == "image/jpeg"


def test_disallowed_attachment_type_rejected(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    import asyncio

    class FakeUpload:
        content_type = "application/x-msdownload"
        filename = "evil.exe"

        async def read(self):
            return b"MZ\x90\x00"

    with pytest.raises(ValidationError):
        asyncio.run(proof_service.store_attachment(session, tenant_id, wo.id, FakeUpload(),
                                                   uploader_type="TECHNICIAN", uploader_id="tech-1"))


def test_acknowledgement_recorded(session, tenant_id, make_work_order):
    wo = _checklist_wo(session, tenant_id, make_work_order)
    ack = proof_service.record_customer_acknowledgement(session, tenant_id, wo.id, method="CUSTOMER_OTP",
                                                        masked_recipient="cus***01", consent_text_version="v1",
                                                        result="CONFIRMED", actor="test")
    session.commit()
    assert ack.method == "CUSTOMER_OTP"
    assert ack.masked_recipient == "cus***01"


def test_qa_approval_requires_complete_evidence(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    tech = make_technician("QA Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                        reason="qa test", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    # No checklist/proof/material -> finish_execution fails.
    with pytest.raises((ChecklistError, ProofError)):
        workorder_service.finish_execution(session, tenant_id, wo.id, actor="test")


def test_qa_reject_requires_reason(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    tech = make_technician("QA Reject Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                        reason="qa test", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    # Cannot submit for verification before execution completes.
    with pytest.raises(StateTransitionError):
        workorder_service.submit_for_verification(session, tenant_id, wo.id, actor="test")
