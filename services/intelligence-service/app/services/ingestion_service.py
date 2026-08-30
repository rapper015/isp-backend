"""Governed ingestion: contract validation, raw events, analytical records,
quarantine, dedup, backfill/replay."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import ContractError, DuplicateError
from ..models import AnalyticalRecord, DataContract, PipelineRun, RawEvent

# Contracts that cannot carry raw PII-sensitive payload into the AI store.
SENSITIVE_CONTRACT_SUBSTRINGS = ("password", "secret", "card", "pan", "otp", "aadhaar")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _checksum(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def get_contract(session: Session, event_name: str) -> DataContract:
    contract = session.scalars(select(DataContract).where(
        DataContract.event_name == event_name, DataContract.state == "ACTIVE")).first()
    return contract


def validate_contract(payload: dict, contract: DataContract) -> list[str]:
    """Return a list of validation errors (empty == valid)."""
    errors = []
    if contract is None:
        return ["unknown contract"]
    payload_keys = set(payload.keys())
    for field in contract.required_fields:
        if field not in payload_keys:
            errors.append(f"missing required field {field}")
    return errors


def ingest_event(session: Session, envelope: dict, *, source: str = "event") -> RawEvent | None:
    """Validate + persist a raw event. Returns None for duplicates/quarantined."""
    event_type = envelope.get("event_type", "")
    event_id = envelope.get("event_id")
    if not event_id:
        raise ContractError("event_id required")
    existing = session.scalars(select(RawEvent).where(RawEvent.event_id == str(event_id))).first()
    if existing is not None:
        return None  # idempotent duplicate

    contract = get_contract(session, event_type)
    payload = envelope.get("payload") or {}
    errors = validate_contract(payload, contract)
    tenant_id = _to_uuid(envelope.get("tenant_id"))
    occurred_at = parse_dt(envelope.get("occurred_at")) or _now()

    if any(marker in event_type.lower() for marker in SENSITIVE_CONTRACT_SUBSTRINGS):
        # Never ingest raw sensitive contracts; quarantine immediately.
        errors.append("sensitive contract not ingested")

    raw = RawEvent(
        tenant_id=tenant_id, event_id=str(event_id), contract=event_type,
        schema_version=str(envelope.get("schema_version", "v1")),
        producer=envelope.get("producer"), event_time=occurred_at, processing_time=_now(),
        correlation_id=envelope.get("correlation_id"), causation_id=envelope.get("causation_id"),
        payload=payload,
        state="QUARANTINED" if errors else "VALID",
        checksum=_checksum(payload), watermark=parse_dt(payload.get("watermark")))
    session.add(raw)
    session.flush()

    if errors:
        return raw  # quarantined; do not create analytical record

    # Normalized analytical record (PII fields stripped).
    normalized = _normalize_payload(payload, contract)
    if contract is not None:
        session.add(AnalyticalRecord(
            tenant_id=tenant_id, contract=event_type,
            entity_type=_entity_type_for(event_type),
            entity_ref=_entity_ref_for(event_type, payload),
            raw_event_id=raw.id, normalized=normalized, event_time=occurred_at, source=source))
        session.flush()
    return raw


def _normalize_payload(payload: dict, contract: DataContract) -> dict:
    """Drop PII fields from the analytical record (kept only in raw event)."""
    if contract is None:
        return payload
    pii = set(contract.pii_fields or [])
    return {k: v for k, v in payload.items() if k not in pii}


def _entity_type_for(event_type: str) -> str:
    if event_type.startswith("crm.customer"):
        return "customer"
    if event_type.startswith("crm.lead"):
        return "lead"
    if event_type.startswith("oss.order"):
        return "order"
    if event_type.startswith("billing."):
        return "customer"
    if event_type.startswith("aaa.session"):
        return "subscriber"
    if event_type.startswith("nas."):
        return "nas"
    if event_type.startswith("network.identity"):
        return "subscriber"
    if event_type.startswith("device.cpe"):
        return "cpe"
    if event_type.startswith("tenancy.tenant"):
        return "tenant"
    if event_type.startswith("assurance."):
        return "assurance"
    return "entity"


def _entity_ref_for(event_type: str, payload: dict) -> str | None:
    candidates = ("customer_id", "subscriber_id", "order_id", "cpe_id", "nas_id", "device_id",
                  "service_id", "tenant_id", "incident_id", "invoice_id", "payment_id", "session_id")
    for key in candidates:
        if payload.get(key):
            return str(payload[key])
    return None


def _to_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def quarantine_count(session: Session, contract: str | None = None) -> int:
    q = select(func.count(RawEvent.id)).where(RawEvent.state == "QUARANTINED")
    if contract:
        q = q.where(RawEvent.contract == contract)
    return session.scalar(q) or 0


def start_pipeline(session: Session, name: str, tenant_id) -> PipelineRun:
    run = PipelineRun(tenant_id=tenant_id, pipeline=name, state="RUNNING", counts={},
                      started_at=_now())
    session.add(run)
    session.flush()
    return run


def finish_pipeline(session: Session, run: PipelineRun, *, state: str = "SUCCEEDED",
                    error: str | None = None) -> PipelineRun:
    run.state = state
    run.error = error
    run.finished_at = _now()
    return run


def replay_raw_events(session: Session, contract: str | None = None, *, limit: int = 5000,
                      state: str | None = None) -> int:
    """Replay raw events into analytical records (idempotent backfill/replay)."""
    q = select(RawEvent).where(RawEvent.state == "VALID")
    if contract:
        q = q.where(RawEvent.contract == contract)
    if state:
        q = q.where(RawEvent.state == state)
    rows = list(session.scalars(q.order_by(RawEvent.event_time).limit(limit)))
    count = 0
    for raw in rows:
        existing = session.scalars(select(AnalyticalRecord).where(
            AnalyticalRecord.raw_event_id == raw.id)).first()
        if existing is not None:
            continue
        contract_obj = get_contract(session, raw.contract)
        session.add(AnalyticalRecord(
            tenant_id=raw.tenant_id, contract=raw.contract,
            entity_type=_entity_type_for(raw.contract),
            entity_ref=_entity_ref_for(raw.contract, raw.payload),
            raw_event_id=raw.id, normalized=_normalize_payload(raw.payload, contract_obj),
            event_time=raw.event_time, source="replay"))
        count += 1
    session.flush()
    return count
