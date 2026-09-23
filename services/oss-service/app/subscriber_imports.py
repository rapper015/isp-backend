"""Auditable CSV import API owned by OSS."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from os import getenv
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import ServiceSubscription, SubscriberImportBatch, SubscriberImportRow
from .security import management_auth
from .services.tenant_service import ensure_oss_tenant

router = APIRouter(prefix="/api/oss/subscriber-imports", dependencies=[Depends(management_auth)])
MAX_BYTES = 10 * 1024 * 1024
ALIASES = {
    "customer name": "full_name", "name": "full_name", "full name": "full_name",
    "mobile": "phone", "primary mobile": "phone", "phone number": "phone",
    "email address": "email", "user name": "username", "subscriber username": "username",
    "package": "package_name", "plan": "package_name", "package name": "package_name",
    "external id": "external_id", "customer id": "customer_id",
}


def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()


def key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    return ALIASES.get(cleaned, cleaned.replace(" ", "_"))


def batch_json(batch: SubscriberImportBatch, session: Session) -> dict:
    summary = batch.summary or {}
    return {"id": str(batch.id), "import_id": str(batch.id), "status": batch.status,
        "franchise_id": batch.franchise_id, "file_name": batch.file_name, "created_at": batch.created_at,
        "completed_at": batch.completed_at, **{name: int(summary.get(name, 0)) for name in
        ("total_rows", "valid_rows", "invalid_rows", "created_rows", "updated_rows", "skipped_rows", "failed_rows", "duplicate_rows")},
        "summary": summary, "warnings": summary.get("warnings", []),
        "error_download_url": f"/api/v1/oss/subscriber-imports/{batch.id}/errors/download" if summary.get("invalid_rows") or summary.get("failed_rows") else ""}


def resolve_franchise(franchise_id: str) -> dict:
    url = getenv("OSS_CRM_BASE_URL", "http://crm-service:8000").rstrip("/")
    secret = getenv("OSS_CRM_INTERNAL_API_KEY", "")
    if not secret: raise HTTPException(503, "OSS to CRM authentication is not configured")
    try:
        response = httpx.get(f"{url}/api/crm/franchises/{franchise_id}", headers={"X-CRM-Service-Key": secret}, timeout=5)
    except httpx.HTTPError as error:
        raise HTTPException(503, "CRM franchise directory is unavailable") from error
    if response.status_code == 404: raise HTTPException(422, "selected franchise does not exist")
    if response.status_code >= 400: raise HTTPException(503, "CRM franchise directory rejected the request")
    return response.json()


@router.post("/validate", status_code=201)
async def validate_import(request: Request, file: UploadFile = File(...), franchise_id: str = Form(...),
    update_existing: bool = Form(True), create_missing_packages: bool = Form(False),
    create_missing_locations: bool = Form(False), dry_run: bool = Form(True), session: Session = Depends(db)):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES: raise HTTPException(413, "CSV exceeds the 10 MiB upload limit")
    if not content: raise HTTPException(422, "CSV file is empty")
    franchise = resolve_franchise(franchise_id)
    tenant_id = franchise.get("tenant_id")
    if not tenant_id: raise HTTPException(503, "CRM franchise response does not include tenant ownership")
    ensure_oss_tenant(session, UUID(str(tenant_id)))
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except UnicodeDecodeError as error:
        raise HTTPException(422, "CSV must use UTF-8 encoding") from error
    if not reader.fieldnames: raise HTTPException(422, "CSV header row is missing")
    batch = SubscriberImportBatch(tenant_id=UUID(str(tenant_id)), franchise_id=franchise_id,
        file_name=file.filename or "subscribers.csv", file_hash=hashlib.sha256(content).hexdigest(),
        options={"update_existing": update_existing, "create_missing_packages": create_missing_packages,
                 "create_missing_locations": create_missing_locations, "dry_run": dry_run},
        created_by=str(request.state.oss_principal.get("subject", "system")))
    session.add(batch); session.flush()
    seen: set[str] = set(); counts = {"create_count": 0, "update_count": 0, "skip_count": 0, "duplicate_rows": 0}
    rows = []
    for number, raw in enumerate(reader, 2):
        normalized = {key(source): (value.strip() if isinstance(value, str) else value) for source, value in raw.items() if source}
        errors = []
        for required in ("full_name", "phone", "username", "package_name"):
            if not normalized.get(required): errors.append({"field": required, "value": "", "error": "Field is required"})
        identity = str(normalized.get("username", "")).casefold()
        if identity and identity in seen:
            errors.append({"field": "username", "value": normalized.get("username"), "error": "Duplicate username within file"}); counts["duplicate_rows"] += 1
        seen.add(identity)
        existing = session.scalar(select(ServiceSubscription).where(ServiceSubscription.tenant_id == batch.tenant_id,
            ServiceSubscription.aaa_subscriber_reference == normalized.get("username"))) if identity else None
        action = "ERROR" if errors else "UPDATE" if existing and update_existing else "SKIP" if existing else "CREATE"
        counts[{"CREATE": "create_count", "UPDATE": "update_count", "SKIP": "skip_count"}.get(action, "skip_count")] += int(action != "ERROR")
        row = SubscriberImportRow(batch_id=batch.id, source_row_number=number, external_id=str(normalized.get("external_id", "")),
            username=str(normalized.get("username", "")), raw_data=raw, normalized_data=normalized, action=action,
            validation_errors=errors, target_subscriber_id=str(existing.id) if existing else None)
        session.add(row); rows.append(row)
    invalid = sum(row.action == "ERROR" for row in rows)
    batch.summary = {"total_rows": len(rows), "valid_rows": len(rows) - invalid, "invalid_rows": invalid,
        "created_rows": 0, "updated_rows": 0, "skipped_rows": 0, "failed_rows": 0, **counts, "warnings": []}
    batch.completed_at = datetime.now(timezone.utc); session.commit(); session.refresh(batch)
    result = batch_json(batch, session); result["sample_rows"] = [row_json(row) for row in rows[:10]]
    return result


def row_json(row: SubscriberImportRow) -> dict:
    return {"id": str(row.id), "source_row_number": row.source_row_number, "external_id": row.external_id,
        "username": row.username, "raw_data": row.raw_data, "normalized_data": row.normalized_data,
        "action": row.action, "validation_errors": row.validation_errors, "processing_error": row.processing_error,
        "target_subscriber_id": row.target_subscriber_id, "processed_at": row.processed_at}


@router.get("")
def list_imports(franchise_id: str | None = None, status: str | None = None, page: int = 1, page_size: int = 50, session: Session = Depends(db)):
    stmt = select(SubscriberImportBatch).order_by(SubscriberImportBatch.created_at.desc())
    if franchise_id: stmt = stmt.where(SubscriberImportBatch.franchise_id == franchise_id)
    if status: stmt = stmt.where(SubscriberImportBatch.status == status.upper())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.offset((max(page, 1)-1)*min(page_size, 250)).limit(min(page_size, 250))).all()
    return {"count": total, "next": None, "previous": None, "results": [batch_json(item, session) for item in items]}


def get_batch(import_id: UUID, session: Session) -> SubscriberImportBatch:
    batch = session.get(SubscriberImportBatch, import_id)
    if not batch: raise HTTPException(404, "subscriber import not found")
    return batch


@router.get("/{import_id}")
def import_detail(import_id: UUID, session: Session = Depends(db)): return batch_json(get_batch(import_id, session), session)


@router.get("/{import_id}/rows")
def import_rows(import_id: UUID, action: str | None = None, page: int = 1, page_size: int = 25, session: Session = Depends(db)):
    get_batch(import_id, session); stmt = select(SubscriberImportRow).where(SubscriberImportRow.batch_id == import_id)
    if action: stmt = stmt.where(SubscriberImportRow.action == action.upper())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(SubscriberImportRow.source_row_number).offset((max(page,1)-1)*page_size).limit(min(page_size,250))).all()
    return {"count": total, "next": None, "previous": None, "results": [row_json(row) for row in rows]}


@router.post("/{import_id}/commit")
def commit_import(import_id: UUID, session: Session = Depends(db)):
    batch = get_batch(import_id, session)
    if batch.status not in {"VALIDATED", "PARTIAL"}: raise HTTPException(409, "import is not ready to commit")
    created = updated = skipped = failed = 0; batch.status = "PROCESSING"
    rows = session.scalars(select(SubscriberImportRow).where(SubscriberImportRow.batch_id == batch.id, SubscriberImportRow.action != "ERROR")).all()
    for row in rows:
        try:
            existing = session.get(ServiceSubscription, UUID(row.target_subscriber_id)) if row.target_subscriber_id else None
            if row.action == "SKIP": skipped += 1; continue
            if existing:
                existing.plan_reference = str(row.normalized_data.get("package_name")); updated += 1; target = existing
            else:
                target = ServiceSubscription(tenant_id=batch.tenant_id, subscription_code=f"IMP-{uuid4().hex[:12].upper()}",
                    status="PENDING_ACTIVATION", customer_id=str(row.normalized_data.get("customer_id") or row.external_id or ""),
                    plan_reference=str(row.normalized_data.get("package_name")), aaa_subscriber_reference=row.username,
                    resource_references={"source": "CSV_IMPORT", "franchise_id": batch.franchise_id})
                session.add(target); session.flush(); created += 1
            row.target_subscriber_id = str(target.id); row.processed_at = datetime.now(timezone.utc)
        except Exception as error:
            row.action = "ERROR"; row.processing_error = str(error)[:2000]; failed += 1
    batch.status = "PARTIAL" if failed else "COMPLETED"; batch.completed_at = datetime.now(timezone.utc)
    batch.summary = {**batch.summary, "created_rows": created, "updated_rows": updated, "skipped_rows": skipped, "failed_rows": failed}
    session.commit(); session.refresh(batch); return batch_json(batch, session)


@router.post("/{import_id}/retry")
def retry_import(import_id: UUID, session: Session = Depends(db)): return commit_import(import_id, session)


@router.post("/{import_id}/cancel")
def cancel_import(import_id: UUID, session: Session = Depends(db)):
    batch = get_batch(import_id, session)
    if batch.status not in {"VALIDATED", "PROCESSING"}: raise HTTPException(409, "import cannot be cancelled in its current state")
    batch.status = "CANCELLED"; batch.completed_at = datetime.now(timezone.utc); session.commit(); return batch_json(batch, session)


@router.get("/{import_id}/errors/download")
def download_errors(import_id: UUID, session: Session = Depends(db)):
    get_batch(import_id, session); rows = session.scalars(select(SubscriberImportRow).where(SubscriberImportRow.batch_id == import_id, SubscriberImportRow.action == "ERROR")).all()
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(["row_number", "username", "field", "value", "error", "original_row_values"])
    for row in rows:
        for error in row.validation_errors or [{"field": "processing", "value": "", "error": row.processing_error}]:
            writer.writerow([row.source_row_number, row.username, error.get("field"), error.get("value"), error.get("error"), json.dumps(row.raw_data)])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="subscriber-import-{import_id}-errors.csv"'})
