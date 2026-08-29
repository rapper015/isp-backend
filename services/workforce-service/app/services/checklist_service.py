"""Versioned checklist execution service.

The work order retains the exact checklist version used during execution; a
published version is immutable. Submission is validated against the item type
and constraints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import checklists as checklist_rules
from ..domain.exceptions import ChecklistError, NotFoundError
from ..models import ChecklistResponse, WorkOrder, WorkOrderChecklist


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_checklist(session: Session, work_order: WorkOrder) -> WorkOrderChecklist | None:
    return session.scalars(
        select(WorkOrderChecklist).where(WorkOrderChecklist.work_order_id == work_order.id)).first()


def get_or_create_checklist(session: Session, tenant_id, work_order: WorkOrder) -> WorkOrderChecklist:
    existing = get_checklist(session, work_order)
    if existing is not None:
        return existing
    snapshot = work_order.checklist_snapshot or {}
    checklist = WorkOrderChecklist(
        tenant_id=tenant_id, work_order_id=work_order.id,
        checklist_template_version=int(snapshot.get("template_version", 1)),
        checklist_snapshot=snapshot,
    )
    session.add(checklist)
    session.flush()
    return checklist


def submit_responses(session: Session, tenant_id, work_order_id: uuid.UUID, *, responses: dict,
                     submitted_by: str | None = None, correlation_id: str | None = None) -> WorkOrderChecklist:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    checklist = get_or_create_checklist(session, tenant_id, work_order)
    items = checklist.checklist_snapshot.get("items", [])

    # Validate the full submission against the snapshot's version.
    valid, errors = checklist_rules.validate_checklist(_snapshot_items(items), responses)
    if not valid:
        raise ChecklistError("checklist validation failed: " + "; ".join(str(e) for e in errors))

    for item in items:
        code = item["code"]
        if code not in responses:
            continue
        existing = session.scalars(select(ChecklistResponse).where(
            ChecklistResponse.checklist_id == checklist.id,
            ChecklistResponse.item_code == code)).first()
        if existing is None:
            existing = ChecklistResponse(tenant_id=tenant_id, checklist_id=checklist.id,
                                         work_order_id=work_order.id, item_code=code,
                                         submitted_by=submitted_by, correlation_id=correlation_id)
            session.add(existing)
        existing.value = responses[code]
        existing.submitted_by = submitted_by or existing.submitted_by
    session.flush()
    return checklist


def _snapshot_items(items: list) -> list:
    """Wrap snapshot item dicts in lightweight objects for the validation rules."""
    from types import SimpleNamespace

    wrapped = []
    for item in items:
        wrapped.append(SimpleNamespace(code=item.get("code"), label=item.get("label"),
                                       item_type=item.get("item_type"), required=item.get("required", False),
                                       rule=item.get("rule", {}), constraints=item.get("constraints", {})))
    return wrapped


def checklist_is_complete(session: Session, tenant_id, work_order: WorkOrder) -> tuple[bool, list[str]]:
    checklist = get_checklist(session, work_order)
    if checklist is None:
        return False, ["no checklist created"]
    items = checklist.checklist_snapshot.get("items", [])
    responses = {r.item_code: r.value for r in session.scalars(
        select(ChecklistResponse).where(ChecklistResponse.checklist_id == checklist.id))}
    valid, errors = checklist_rules.validate_checklist(_snapshot_items(items), responses)
    if valid:
        checklist.completed = True
        checklist.completed_at = _now()
        session.flush()
    return valid, errors


def responses_for_checklist(session: Session, tenant_id, work_order_id: uuid.UUID) -> list[ChecklistResponse]:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    checklist = get_checklist(session, work_order)
    if checklist is None:
        return []
    return list(session.scalars(select(ChecklistResponse).where(ChecklistResponse.checklist_id == checklist.id)))
