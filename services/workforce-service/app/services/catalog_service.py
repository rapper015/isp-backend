"""Catalogue service: work-order types, versioned templates, versioned checklist
templates, service areas and field SLA policies.

A platform-wide global default catalogue (tenant_id = NULL) is created on
startup so a fresh tenant has a working, governed field configuration."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.sla.calendar import default_working_hours
from ..enums import WORK_ORDER_TYPES
from ..models import (
    BusinessCalendar,
    ChecklistItem,
    ChecklistTemplate,
    ChecklistTemplateVersion,
    FieldSLAPolicy,
    FieldSLAPolicyVersion,
    FieldSLATarget,
    WorkOrderTemplate,
    WorkOrderTemplateVersion,
    WorkOrderType,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Default checklist items per work-order type (documented baseline).
_DEFAULT_CHECKLISTS = {
    "NEW_INSTALLATION": [
        ("VERIFY_CUSTOMER", "Verify customer identity / KYC reference", "CHECKBOX", True),
        ("INSPECT_SITE", "Inspect physical feasibility", "CHECKBOX", True),
        ("INSTALL_CABLE", "Install cable/fiber", "CHECKBOX", True),
        ("INSTALL_ONT", "Install ONT/ONU", "CHECKBOX", True),
        ("SCAN_SERIAL", "Scan device serial number", "SERIAL_NUMBER", True),
        ("RECORD_MAC", "Record device MAC address", "MAC_ADDRESS", True),
        ("OPTICAL_READING", "Capture optical/signal reading", "OPTICAL_READING", True),
        ("SERVICE_TEST", "Run service speed test", "SPEED_TEST", True),
        ("PHOTO_INSTALLATION", "Capture installation photograph", "PHOTO", True),
        ("CUSTOMER_ACK", "Capture customer acknowledgement", "SIGNATURE", True),
        ("MATERIALS_USED", "Record materials used", "TEXT", True),
    ],
    "FAULT_REPAIR": [
        ("VERIFY_CUSTOMER", "Verify customer identity", "CHECKBOX", True),
        ("INSPECT_POWER", "Inspect power", "CHECKBOX", True),
        ("INSPECT_CABLE", "Inspect cable/fiber", "CHECKBOX", True),
        ("INSPECT_ONT", "Inspect ONT/ONU", "CHECKBOX", True),
        ("SIGNAL_LEVELS", "Capture signal levels", "SIGNAL_READING", True),
        ("ROOT_CAUSE", "Record root cause / result code", "SELECT", True),
        ("PHOTO_REPAIR", "Capture repair evidence", "PHOTO", True),
        ("CUSTOMER_ACK", "Capture customer acknowledgement", "SIGNATURE", True),
    ],
    "SITE_SURVEY": [
        ("LOCATION_VERIFIED", "Verify service location", "CHECKBOX", True),
        ("GPS_CAPTURE", "Capture site GPS", "GPS_CAPTURE", True),
        ("NETWORK_FEASIBILITY", "Assess network feasibility", "CHECKBOX", True),
        ("CABLE_ROUTE", "Confirm cable/fiber route", "CHECKBOX", True),
        ("ESTIMATED_LENGTH", "Estimated cable length (m)", "NUMBER", True),
        ("REQUIRED_EQUIPMENT", "Required equipment", "TEXT", False),
        ("PERMISSION_OK", "Building permission confirmed", "CHECKBOX", True),
        ("PHOTO_SURVEY", "Survey photographs", "PHOTO", True),
        ("RECOMMENDATION", "Survey recommendation", "SELECT", True),
    ],
}
_DEFAULT_SURVEY_RECOMMENDATIONS = ["APPROVED", "APPROVED_WITH_CONDITIONS", "REJECTED"]

# Default work-order template definitions.
_DEFAULT_TEMPLATES = {
    "NEW_INSTALLATION": {
        "required_skills": ["FIBER_INSTALL", "ONT_INSTALL"],
        "required_certifications": ["FIBER_SAFETY"],
        "expected_duration_minutes": 120,
        "required_equipment": ["ONT", "CABLE"],
        "required_consumables": ["FIBER_CONNECTOR", "SPLICE"],
        "sla_policy_code": "FIELD_DEFAULT",
        "completion_rules": {"require_qa": True, "require_acknowledgement": True,
                             "require_proof": ["PHOTOGRAPH", "SERIAL_NUMBER", "CUSTOMER_ACKNOWLEDGEMENT"],
                             "require_remote_activation": True},
        "checklist_code": "NEW_INSTALLATION",
    },
    "FAULT_REPAIR": {
        "required_skills": ["FIBER_INSTALL"],
        "required_certifications": [],
        "expected_duration_minutes": 90,
        "required_equipment": [],
        "required_consumables": [],
        "sla_policy_code": "FIELD_DEFAULT",
        "completion_rules": {"require_qa": False, "require_acknowledgement": True,
                             "require_proof": ["PHOTOGRAPH"], "require_remote_activation": False},
        "checklist_code": "FAULT_REPAIR",
    },
    "SITE_SURVEY": {
        "required_skills": ["SURVEY"],
        "required_certifications": [],
        "expected_duration_minutes": 60,
        "required_equipment": [],
        "required_consumables": [],
        "sla_policy_code": "FIELD_DEFAULT",
        "completion_rules": {"require_qa": False, "require_acknowledgement": False,
                             "require_proof": ["PHOTOGRAPH"], "require_remote_activation": False},
        "checklist_code": "SITE_SURVEY",
    },
}
_FALLBACK_TEMPLATE = {
    "required_skills": [],
    "required_certifications": [],
    "expected_duration_minutes": 60,
    "required_equipment": [],
    "required_consumables": [],
    "sla_policy_code": "FIELD_DEFAULT",
    "completion_rules": {"require_qa": False, "require_acknowledgement": True,
                         "require_proof": [], "require_remote_activation": False},
    "checklist_code": None,
}


def ensure_global_defaults(session: Session) -> None:
    if session.scalars(select(WorkOrderType).limit(1)).first() is None:
        for code in WORK_ORDER_TYPES:
            session.add(WorkOrderType(code=code, name=code.replace("_", " ").title(),
                                      requires_remote_activation=code in ("NEW_INSTALLATION", "ONT_INSTALLATION",
                                                                           "ROUTER_INSTALLATION"),
                                      is_active=True, sort_order=0))
    if session.scalars(select(ChecklistTemplate).limit(1)).first() is None:
        _seed_checklist_templates(session)
    if session.scalars(select(WorkOrderTemplate).limit(1)).first() is None:
        _seed_work_order_templates(session)
    if session.scalars(select(BusinessCalendar).limit(1)).first() is None:
        session.add(BusinessCalendar(code="DEFAULT", name="Default Business Hours", timezone="UTC",
                                     working_hours=default_working_hours(), is_active=True))
    if session.scalars(select(FieldSLAPolicy).limit(1)).first() is None:
        _seed_field_sla_policy(session)
    session.flush()


def _seed_checklist_templates(session: Session) -> None:
    for wo_type, items in _DEFAULT_CHECKLISTS.items():
        template = ChecklistTemplate(code=wo_type, name=f"{wo_type.replace('_', ' ').title()} Checklist",
                                     work_order_type=wo_type, current_version=1)
        session.add(template)
        session.flush()
        version = ChecklistTemplateVersion(tenant_id=None, template_id=template.id, version=1,
                                           is_active=True, activated_at=_now())
        session.add(version)
        session.flush()
        for sort, (code, label, item_type, required) in enumerate(items):
            session.add(ChecklistItem(tenant_id=None, version_id=version.id, code=code, label=label,
                                      item_type=item_type, required=required, rule={},
                                      constraints=_constraints_for(item_type, code), sort_order=sort))
    session.flush()


def _constraints_for(item_type: str, code: str) -> dict:
    if item_type == "OPTICAL_READING":
        return {"expected_range": [-30, 0], "unit": "dBm"}
    if item_type == "SIGNAL_READING":
        return {"expected_range": [-30, 0], "unit": "dBm"}
    if item_type == "SPEED_TEST":
        return {"min": 0}
    if item_type == "SELECT" and code == "RECOMMENDATION":
        return {"allowed_values": _DEFAULT_SURVEY_RECOMMENDATIONS}
    if item_type == "SELECT" and code == "ROOT_CAUSE":
        return {"allowed_values": ["ONT_FAULT", "CABLE_FAULT", "ROUTER_FAULT", "PORT_FAULT", "OTHER"]}
    return {}


def _seed_work_order_templates(session: Session) -> None:
    for wo_type, definition in _DEFAULT_TEMPLATES.items():
        template = WorkOrderTemplate(code=wo_type, name=f"{wo_type.replace('_', ' ').title()} Template",
                                     work_order_type=wo_type, is_active=True, current_version=1)
        session.add(template)
        session.flush()
        checklist_code = definition.get("checklist_code")
        checklist_template_id = None
        if checklist_code:
            checklist = session.scalars(
                select(ChecklistTemplate).where(ChecklistTemplate.code == checklist_code)).first()
            checklist_template_id = str(checklist.id) if checklist else None
        version = WorkOrderTemplateVersion(
            tenant_id=None, template_id=template.id, version=1, is_active=True, activated_at=_now(),
            definition={**definition, "checklist_template_id": checklist_template_id})
        session.add(version)
    session.flush()


def _seed_field_sla_policy(session: Session) -> None:
    policy = FieldSLAPolicy(code="FIELD_DEFAULT", name="Default Field SLA", is_active=True, current_version=1)
    session.add(policy)
    session.flush()
    version = FieldSLAPolicyVersion(
        tenant_id=None, policy_id=policy.id, version=1, is_active=True, activated_at=_now(),
        definition={
            "pause_on_states": ["CUSTOMER_UNAVAILABLE", "AWAITING_PARTS", "AWAITING_REMOTE_ACTION", "RESCHEDULE_REQUIRED"],
            "reopen_policy": "RESTART",
            "escalation": [
                {"target": "ARRIVAL", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_DISPATCHER"},
                {"target": "TIME_TO_COMPLETE", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_SUPERVISOR"},
                {"target": "TIME_TO_COMPLETE", "at_risk_pct": 90, "level": 2, "action": "REQUIRE_MANUAL_INTERVENTION"},
            ],
        })
    session.add(version)
    session.flush()
    session.add(FieldSLATarget(tenant_id=None, version_id=version.id, priority="ALL", kind="ARRIVAL", business_seconds=4 * 3600))
    session.add(FieldSLATarget(tenant_id=None, version_id=version.id, priority="ALL", kind="TIME_TO_COMPLETE", business_seconds=8 * 3600))
    session.add(FieldSLATarget(tenant_id=None, version_id=version.id, priority="P1_CRITICAL", kind="ARRIVAL", business_seconds=30 * 60))
    session.add(FieldSLATarget(tenant_id=None, version_id=version.id, priority="P1_CRITICAL", kind="TIME_TO_COMPLETE", business_seconds=4 * 3600))
    session.flush()


def ensure_tenant_defaults(session: Session, tenant_id) -> None:
    ensure_global_defaults(session)
    existing = session.scalars(select(BusinessCalendar).where(BusinessCalendar.tenant_id == tenant_id)).first()
    if existing is not None:
        session.flush()
        return
    global_cal = session.scalars(select(BusinessCalendar).where(BusinessCalendar.tenant_id.is_(None))).first()
    session.add(BusinessCalendar(tenant_id=tenant_id, code="DEFAULT", name="Default Business Hours",
                                 timezone="UTC", working_hours=global_cal.working_hours if global_cal else default_working_hours(),
                                 is_active=True))
    session.flush()


def get_or_create_calendar(session: Session, tenant_id, code: str = "DEFAULT") -> BusinessCalendar:
    calendar = session.scalars(
        select(BusinessCalendar).where(BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.code == code)).first()
    if calendar is None:
        global_cal = session.scalars(
            select(BusinessCalendar).where(BusinessCalendar.tenant_id.is_(None), BusinessCalendar.code == code)).first()
        calendar = BusinessCalendar(
            tenant_id=tenant_id, code=code,
            name=global_cal.name if global_cal else "Default Business Hours",
            timezone=global_cal.timezone if global_cal else "UTC",
            working_hours=global_cal.working_hours if global_cal else default_working_hours(),
            is_active=True)
        session.add(calendar)
        session.flush()
    return calendar


def resolve_template(session: Session, tenant_id, work_order_type: str) -> tuple[WorkOrderTemplateVersion, dict]:
    """Return (active template version, normalized definition) for a type."""
    template = session.scalars(
        select(WorkOrderTemplate).where(WorkOrderTemplate.work_order_type == work_order_type,
                                        WorkOrderTemplate.is_active.is_(True))).first()
    if template is None:
        # Deterministic fallback template so any configured type can proceed.
        from ..models import WorkOrderTemplate as _T

        template = _T(code=work_order_type, name=work_order_type, work_order_type=work_order_type,
                      is_active=True, current_version=0)
        session.add(template)
        session.flush()
        version = WorkOrderTemplateVersion(tenant_id=None, template_id=template.id, version=1,
                                           is_active=True, activated_at=_now(), definition=_FALLBACK_TEMPLATE)
        session.add(version)
        session.flush()
        return version, _FALLBACK_TEMPLATE
    version = session.scalars(
        select(WorkOrderTemplateVersion).where(WorkOrderTemplateVersion.template_id == template.id,
                                               WorkOrderTemplateVersion.is_active.is_(True))).first()
    if version is None:
        raise ValueError(f"work-order template {work_order_type!r} has no active version")
    return version, version.definition


def resolve_checklist(session: Session, checklist_template_id: str | None, work_order_type: str):
    """Return (version_id, items) for the active checklist template."""
    if checklist_template_id:
        version = session.get(ChecklistTemplateVersion, uuid_or_none(checklist_template_id))
        if version is not None:
            items = list(session.scalars(
                select(ChecklistItem).where(ChecklistItem.version_id == version.id).order_by(ChecklistItem.sort_order)))
            return version, items
    template = session.scalars(
        select(ChecklistTemplate).where(ChecklistTemplate.work_order_type == work_order_type)).first()
    if template is None:
        return None, []
    version = session.scalars(
        select(ChecklistTemplateVersion).where(ChecklistTemplateVersion.template_id == template.id,
                                               ChecklistTemplateVersion.is_active.is_(True))).first()
    if version is None:
        return None, []
    items = list(session.scalars(
        select(ChecklistItem).where(ChecklistItem.version_id == version.id).order_by(ChecklistItem.sort_order)))
    return version, items


def uuid_or_none(value):
    if not value:
        return None
    import uuid

    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
