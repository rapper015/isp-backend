"""Capability-aware diagnostics: supported/unsupported, offline vs failed,
results with evaluation, and support-ticket linkage."""
import pytest

from app.domain.exceptions import DiagnosticError
from app.models import DiagnosticJob, DiagnosticResult
from app.services import diagnostic_service
from app.integrations.fakes import STATE
from conftest import variant_for


@pytest.fixture
def cpe(session, tenant_id, acs, make_acs_device, make_device):
    device, acs_device_id = make_device(serial="SN-DIAG", product_class="AN5506")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    session.commit()
    return {"device": device, "acs_device_id": acs_device_id, "client": acs["client"]}


def test_supported_diagnostics_list(session, tenant_id, cpe):
    supported = diagnostic_service.supported_diagnostics(session, tenant_id, cpe["device"].id)
    assert "PING" in supported
    assert "TRACEROUTE" in supported


def test_unsupported_diagnostic_flagged(session, tenant_id, cpe):
    job = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                   diagnostic_type="SELF_TEST", requested_by="support")
    assert job.state == "UNSUPPORTED"
    with pytest.raises(DiagnosticError):
        diagnostic_service.run_diagnostic(session, tenant_id, job.id, actor="test")


def test_successful_ping_diagnostic(session, tenant_id, cpe):
    job = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                   diagnostic_type="PING", requested_by="support")
    job = diagnostic_service.run_diagnostic(session, tenant_id, job.id, actor="test")
    job = diagnostic_service.complete_diagnostic(session, tenant_id, job.id,
                                                 raw={"success": True, "average_rtt_ms": 5, "packet_loss_percent": 0},
                                                 actor="test")
    assert job.state == "SUCCEEDED"
    result = session.query(DiagnosticResult).filter_by(job_id=job.id).first()
    assert result.evaluation == "PASS"
    assert result.normalized_result["average_rtt_ms"] == 5


def test_offline_device_times_out_not_failed(session, tenant_id, cpe):
    job = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                   diagnostic_type="PING", requested_by="support")
    cpe["client"].set_connection_request_outcome("UNREACHABLE")
    job = diagnostic_service.run_diagnostic(session, tenant_id, job.id, actor="test")
    assert job.state == "WAITING_FOR_DEVICE"
    job = diagnostic_service.complete_diagnostic(session, tenant_id, job.id, offline=True, actor="test")
    assert job.state == "TIMED_OUT"
    result = session.query(DiagnosticResult).filter_by(job_id=job.id).first()
    assert result.offline is True
    assert result.evaluation == "UNKNOWN"


def test_failed_diagnostic(session, tenant_id, cpe):
    job = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                   diagnostic_type="PING", requested_by="support")
    diagnostic_service.run_diagnostic(session, tenant_id, job.id, actor="test")
    job = diagnostic_service.complete_diagnostic(session, tenant_id, job.id, failed=True,
                                                 fault_code="DIAG_FAILED", actor="test")
    assert job.state == "FAILED"
    assert job.failure_code == "DIAG_FAILED"


def test_support_ticket_linkage(session, tenant_id, cpe):
    job = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                   diagnostic_type="WAN_STATUS", requested_by="support",
                                                   support_ticket_id="TKT-1")
    diagnostic_service.run_diagnostic(session, tenant_id, job.id, actor="test")
    diagnostic_service.complete_diagnostic(session, tenant_id, job.id,
                                           raw={"status": "UP", "wan_ip": "1.2.3.4"}, actor="test")
    # The job retained the support-ticket reference for timeline linkage.
    job = diagnostic_service.get_job_or_404(session, tenant_id, job.id)
    assert job.support_ticket_id == "TKT-1"


def test_idempotent_diagnostic_create(session, tenant_id, cpe):
    job1 = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                    diagnostic_type="PING", requested_by="support",
                                                    idempotency_key="diag-idem-1")
    job2 = diagnostic_service.create_diagnostic_job(session, tenant_id, cpe["device"].id,
                                                    diagnostic_type="PING", requested_by="support",
                                                    idempotency_key="diag-idem-1")
    assert job1.id == job2.id
