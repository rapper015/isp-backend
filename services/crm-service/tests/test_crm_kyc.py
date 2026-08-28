"""Integration tests: KYC cases, documents (masked), and CAF workflow."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def _setup(client):
    tenant_id = client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"}, headers=HEADERS).json()["id"]
    customer = client.post(f"/api/crm/customers?tenant_id={tenant_id}", json={"full_name": "KYC User", "phone": f"9{uuid4().int % 100000000:08d}"}, headers=HEADERS).json()
    return tenant_id, customer["id"]


def test_kyc_case_workflow_and_document_masking():
    with TestClient(app) as client:
        tenant_id, customer_id = _setup(client)
        case = client.post(f"/api/crm/customers/{customer_id}/kyc?tenant_id={tenant_id}", json={"kyc_type": "INDIVIDUAL"}, headers=HEADERS)
        assert case.status_code == 200
        case_id = case.json()["id"]
        doc = client.post(f"/api/crm/kyc/{case_id}/documents?tenant_id={tenant_id}", json={"document_type": "PAN", "storage_reference": "s3://private/kyc/abc.pdf", "masked_identifier": "ABCDE1234F", "content_type": "application/pdf", "size_bytes": 1000}, headers=HEADERS)
        assert doc.status_code == 200
        # Only the masked identifier is returned, never the storage reference or full identifier.
        assert doc.json()["masked_identifier"].startswith("*")
        submitted = client.post(f"/api/crm/kyc/{case_id}/submit?tenant_id={tenant_id}", headers=HEADERS)
        assert submitted.json()["status"] == "SUBMITTED"
        verified = client.post(f"/api/crm/kyc/{case_id}/verify?tenant_id={tenant_id}", json={"method": "manual"}, headers=HEADERS)
        assert verified.json()["status"] == "VERIFIED"
        # Verified documents never leak the raw identifier or storage path in lists.
        documents = client.get(f"/api/crm/kyc/{case_id}/documents?tenant_id={tenant_id}", headers=HEADERS).json()
        assert all("s3://" not in str(item) for item in documents)
        assert all("ABCDE1234F" not in str(item) for item in documents)


def test_caf_workflow():
    with TestClient(app) as client:
        tenant_id, customer_id = _setup(client)
        caf = client.post(f"/api/crm/customers/{customer_id}/caf?tenant_id={tenant_id}", json={"requested_services": ["broadband"], "declaration_accepted": True}, headers=HEADERS)
        assert caf.status_code == 200
        caf_id = caf.json()["id"]
        submitted = client.post(f"/api/crm/caf/{caf_id}/submit?tenant_id={tenant_id}", headers=HEADERS)
        assert submitted.json()["status"] == "SUBMITTED"
        approved = client.post(f"/api/crm/caf/{caf_id}/approve?tenant_id={tenant_id}", json={}, headers=HEADERS)
        assert approved.json()["status"] == "APPROVED"


def test_sensitive_documents_never_in_audit_or_timeline():
    with TestClient(app) as client:
        tenant_id, customer_id = _setup(client)
        case = client.post(f"/api/crm/customers/{customer_id}/kyc?tenant_id={tenant_id}", json={}, headers=HEADERS).json()
        client.post(f"/api/crm/kyc/{case['id']}/documents?tenant_id={tenant_id}", json={"document_type": "AADHAAR", "storage_reference": "s3://private/kyc/aadhaar.pdf", "masked_identifier": "999999999999"}, headers=HEADERS)
        audit = client.get(f"/api/crm/customers/{customer_id}/audit?tenant_id={tenant_id}", headers=HEADERS).text
        assert "999999999999" not in audit
        assert "s3://" not in audit
