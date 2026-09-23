"""Auditable invoice CSV imports owned by the BSS service."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from os import getenv
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import InvoiceImportBatch, InvoiceImportRow
from .revenue.models import BillingAccount, RevenueInvoice, Tenant
from .revenue.security import management_auth

router = APIRouter(prefix="/api/bss/invoice-imports", dependencies=[Depends(management_auth)])
MAX_BYTES = 10 * 1024 * 1024
ALIASES = {
    "invoice no": "invoice_number", "invoice number": "invoice_number",
    "user name": "username", "subscriber username": "username",
    "package name": "package_name", "plan": "package_name", "plan name": "package_name",
    "invoice date": "invoice_date", "issued at": "invoice_date",
    "due date": "due_date", "bill from": "billing_period_start", "bill to": "billing_period_end",
    "final invoice amount": "amount", "invoice amount": "amount", "total amount": "amount",
    "paid amount": "paid_amount", "customer name": "customer_name", "franchise name": "franchise_name",
}


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def field_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    return ALIASES.get(cleaned, cleaned.replace(" ", "_"))


def parse_date(value: str, field: str, errors: list[dict]) -> str | None:
    for pattern in (None, "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if pattern is None else datetime.strptime(value, pattern)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
        except ValueError:
            continue
    errors.append({"field": field, "value": value, "error": "Unsupported date format"})
    return None


def resolve_franchise(franchise_id: str) -> dict:
    secret = getenv("BSS_CRM_INTERNAL_API_KEY", "")
    if not secret:
        raise HTTPException(503, "BSS to CRM authentication is not configured")
    try:
        response = httpx.get(
            f"{getenv('BSS_CRM_BASE_URL', 'http://crm-service:8000').rstrip('/')}/api/crm/franchises/{franchise_id}",
            headers={"X-CRM-Service-Key": secret}, timeout=5,
        )
    except httpx.HTTPError as error:
        raise HTTPException(503, "CRM franchise directory is unavailable") from error
    if response.status_code == 404:
        raise HTTPException(422, "selected franchise does not exist")
    if response.status_code >= 400:
        raise HTTPException(503, "CRM franchise directory rejected the request")
    return response.json()


def batch_json(batch: InvoiceImportBatch) -> dict:
    summary = batch.summary or {}
    names = ("total_rows", "valid_rows", "invalid_rows", "created_rows", "updated_rows", "skipped_rows", "failed_rows", "duplicate_rows")
    return {
        "id": str(batch.id), "import_id": str(batch.id), "status": batch.status,
        "franchise_id": batch.franchise_id, "file_name": batch.file_name,
        "created_at": batch.created_at, "completed_at": batch.completed_at,
        **{name: int(summary.get(name, 0)) for name in names},
        "summary": summary, "warnings": summary.get("warnings", []),
        "error_download_url": f"/api/v1/bss/invoice-imports/{batch.id}/errors/download" if summary.get("invalid_rows") or summary.get("failed_rows") else "",
    }


def row_json(row: InvoiceImportRow) -> dict:
    return {
        "id": str(row.id), "source_row_number": row.source_row_number,
        "source_invoice_number": row.source_invoice_number, "username": row.username,
        "raw_data": row.raw_data, "normalized_data": row.normalized_data, "action": row.action,
        "validation_errors": row.validation_errors, "processing_error": row.processing_error,
        "target_invoice_id": row.target_invoice_id, "processed_at": row.processed_at,
    }


@router.post("/validate", status_code=201)
async def validate_import(request: Request, file: UploadFile = File(...), franchise_id: str = Form(...),
    update_existing: bool = Form(True), create_missing_packages: bool = Form(False),
    dry_run: bool = Form(True), session: Session = Depends(db)):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "CSV exceeds the 10 MiB upload limit")
    if not content:
        raise HTTPException(422, "CSV file is empty")
    franchise = resolve_franchise(franchise_id)
    tenant_id = UUID(str(franchise.get("tenant_id")))
    if not session.get(Tenant, tenant_id):
        session.add(Tenant(id=tenant_id, name=franchise.get("name") or "Imported tenant", code=f"IMP-{str(tenant_id)[:8]}"))
        session.flush()
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    except UnicodeDecodeError as error:
        raise HTTPException(422, "CSV must use UTF-8 encoding") from error
    if not reader.fieldnames:
        raise HTTPException(422, "CSV header row is missing")
    batch = InvoiceImportBatch(
        tenant_id=tenant_id, franchise_id=franchise_id, file_name=file.filename or "invoices.csv",
        file_hash=hashlib.sha256(content).hexdigest(),
        options={"update_existing": update_existing, "create_missing_packages": create_missing_packages, "dry_run": dry_run},
        created_by=str(request.state.bss_principal.get("subject", "system")),
    )
    session.add(batch); session.flush()
    seen: set[str] = set(); rows: list[InvoiceImportRow] = []; creates = updates = skips = duplicates = 0
    for number, raw in enumerate(reader, 2):
        data = {field_key(key): value.strip() for key, value in raw.items() if key and isinstance(value, str)}
        errors: list[dict] = []
        for required in ("invoice_number", "username", "package_name", "invoice_date", "due_date", "amount", "paid_amount"):
            if not data.get(required):
                errors.append({"field": required, "value": "", "error": "Field is required"})
        for name in ("amount", "paid_amount"):
            try:
                data[name] = str(Decimal(data.get(name, "0").replace(",", "")))
            except InvalidOperation:
                errors.append({"field": name, "value": data.get(name), "error": "Invalid decimal value"})
        if not errors:
            if Decimal(data["amount"]) < 0 or Decimal(data["paid_amount"]) < 0 or Decimal(data["paid_amount"]) > Decimal(data["amount"]):
                errors.append({"field": "paid_amount", "value": data["paid_amount"], "error": "Paid amount must be between zero and invoice amount"})
        for name in ("invoice_date", "due_date"):
            if data.get(name):
                parsed = parse_date(data[name], name, errors)
                if parsed: data[name] = parsed
        identity = data.get("invoice_number", "").casefold()
        if identity in seen:
            errors.append({"field": "invoice_number", "value": data.get("invoice_number"), "error": "Duplicate invoice number within file"}); duplicates += 1
        seen.add(identity)
        existing = session.scalar(select(RevenueInvoice).where(RevenueInvoice.tenant_id == tenant_id, RevenueInvoice.invoice_number == data.get("invoice_number"))) if identity else None
        action = "ERROR" if errors else "UPDATE" if existing and update_existing else "SKIP" if existing else "CREATE"
        creates += action == "CREATE"; updates += action == "UPDATE"; skips += action == "SKIP"
        row = InvoiceImportRow(batch_id=batch.id, source_row_number=number, source_invoice_number=data.get("invoice_number", ""),
            username=data.get("username", ""), raw_data=raw, normalized_data=data, action=action,
            validation_errors=errors, target_invoice_id=str(existing.id) if existing else None)
        session.add(row); rows.append(row)
    invalid = sum(row.action == "ERROR" for row in rows)
    batch.summary = {"total_rows": len(rows), "valid_rows": len(rows)-invalid, "invalid_rows": invalid,
        "created_rows": 0, "updated_rows": 0, "skipped_rows": 0, "failed_rows": 0, "duplicate_rows": duplicates,
        "create_count": creates, "update_count": updates, "skip_count": skips, "warnings": []}
    batch.completed_at = datetime.now(timezone.utc); session.commit(); session.refresh(batch)
    result = batch_json(batch); result["sample_rows"] = [row_json(row) for row in rows[:10]]
    return result


def get_batch(import_id: UUID, session: Session) -> InvoiceImportBatch:
    batch = session.get(InvoiceImportBatch, import_id)
    if not batch: raise HTTPException(404, "invoice import not found")
    return batch


@router.get("")
def list_imports(franchise_id: str | None = None, status: str | None = None, page: int = 1, page_size: int = 50, session: Session = Depends(db)):
    stmt = select(InvoiceImportBatch).order_by(InvoiceImportBatch.created_at.desc())
    if franchise_id: stmt = stmt.where(InvoiceImportBatch.franchise_id == franchise_id)
    if status: stmt = stmt.where(InvoiceImportBatch.status == status.upper())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.offset((max(page, 1)-1)*min(page_size, 250)).limit(min(page_size, 250))).all()
    return {"count": total, "next": None, "previous": None, "results": [batch_json(item) for item in items]}


@router.get("/{import_id}")
def import_detail(import_id: UUID, session: Session = Depends(db)): return batch_json(get_batch(import_id, session))


@router.get("/{import_id}/rows")
def import_rows(import_id: UUID, action: str | None = None, page: int = 1, page_size: int = 25, session: Session = Depends(db)):
    get_batch(import_id, session); stmt = select(InvoiceImportRow).where(InvoiceImportRow.batch_id == import_id)
    if action: stmt = stmt.where(InvoiceImportRow.action == action.upper())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(InvoiceImportRow.source_row_number).offset((max(page,1)-1)*page_size).limit(min(page_size,250))).all()
    return {"count": total, "next": None, "previous": None, "results": [row_json(row) for row in rows]}


@router.post("/{import_id}/commit")
def commit_import(import_id: UUID, session: Session = Depends(db)):
    batch = get_batch(import_id, session)
    if batch.status not in {"VALIDATED", "PARTIAL"}: raise HTTPException(409, "import is not ready to commit")
    created = updated = skipped = failed = 0; batch.status = "PROCESSING"
    rows = session.scalars(select(InvoiceImportRow).where(InvoiceImportRow.batch_id == batch.id, InvoiceImportRow.action != "ERROR")).all()
    for row in rows:
        try:
            if row.action == "SKIP": skipped += 1; continue
            data = row.normalized_data
            account = session.scalar(select(BillingAccount).where(BillingAccount.tenant_id == batch.tenant_id, BillingAccount.account_code == row.username))
            if not account:
                account = BillingAccount(tenant_id=batch.tenant_id, account_code=row.username, customer_ref=data.get("customer_id") or row.username)
                session.add(account); session.flush()
            invoice = session.get(RevenueInvoice, UUID(row.target_invoice_id)) if row.target_invoice_id else None
            values = {"billing_account_id": account.id, "currency": "INR", "total_amount": Decimal(data["amount"]),
                "paid_amount": Decimal(data["paid_amount"]), "status": "PAID" if Decimal(data["paid_amount"]) >= Decimal(data["amount"]) else "PARTIALLY_PAID" if Decimal(data["paid_amount"]) else "ISSUED",
                "issued_at": datetime.fromisoformat(data["invoice_date"]), "due_date": datetime.fromisoformat(data["due_date"]),
                "plan_reference": data.get("package_name"), "external_reference": row.source_invoice_number,
                "meta": {"source": "LEGACY_INVOICE_CSV", "franchise_id": batch.franchise_id, "raw": row.raw_data}}
            if invoice:
                for key, value in values.items(): setattr(invoice, key, value)
                updated += 1
            else:
                invoice = RevenueInvoice(tenant_id=batch.tenant_id, invoice_number=row.source_invoice_number[:64], **values)
                session.add(invoice); session.flush(); created += 1
            row.target_invoice_id = str(invoice.id); row.processed_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as error:
            session.rollback(); batch = get_batch(import_id, session); row = session.get(InvoiceImportRow, row.id)
            row.action = "ERROR"; row.processing_error = str(error)[:2000]; failed += 1
            session.commit()
    batch = get_batch(import_id, session)
    batch.status = "PARTIAL" if failed else "COMPLETED"; batch.completed_at = datetime.now(timezone.utc)
    batch.summary = {**batch.summary, "created_rows": created, "updated_rows": updated, "skipped_rows": skipped, "failed_rows": failed}
    session.commit(); session.refresh(batch); return batch_json(batch)


@router.post("/{import_id}/retry")
def retry_import(import_id: UUID, session: Session = Depends(db)): return commit_import(import_id, session)


@router.post("/{import_id}/cancel")
def cancel_import(import_id: UUID, session: Session = Depends(db)):
    batch = get_batch(import_id, session)
    if batch.status not in {"VALIDATED", "PROCESSING"}: raise HTTPException(409, "import cannot be cancelled in its current state")
    batch.status = "CANCELLED"; batch.completed_at = datetime.now(timezone.utc); session.commit(); return batch_json(batch)


@router.get("/{import_id}/errors/download")
def download_errors(import_id: UUID, session: Session = Depends(db)):
    get_batch(import_id, session); rows = session.scalars(select(InvoiceImportRow).where(InvoiceImportRow.batch_id == import_id, InvoiceImportRow.action == "ERROR")).all()
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(["row_number", "invoice_number", "username", "field", "value", "error", "original_row_values"])
    for row in rows:
        for error in row.validation_errors or [{"field": "processing", "value": "", "error": row.processing_error}]:
            writer.writerow([row.source_row_number, row.source_invoice_number, row.username, error.get("field"), error.get("value"), error.get("error"), json.dumps(row.raw_data)])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="invoice-import-{import_id}-errors.csv"'})
