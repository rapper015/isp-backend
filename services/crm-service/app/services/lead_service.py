"""Lead pipeline service: capture, duplicate detection, assignment,
transitions, qualification, feasibility, follow-ups and reopening.

Stage changes must go through the state machine. Direct status patching is not
allowed."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import FEASIBILITY_STATES, FOLLOWUP_STATUSES, INTERACTION_CHANNELS, LEAD_PRIORITIES, LEAD_SOURCES, LEAD_STAGES, LEAD_TYPES
from ..models import FollowUp, Lead, LeadAssignment, LeadInteraction, LeadStageHistory
from ..state_machine import lead_transition
from ..validation import ValidationError, normalize_email, normalize_phone
from .audit_service import audit, correlation, outbox, timeline

LEAD_TERMINAL_STAGES = {"CONVERTED", "LOST", "DISQUALIFIED", "DUPLICATE"}


def _lead_number(lead: Lead) -> str:
    return f"LD-{lead.id.hex[:10].upper()}"


def capture_lead(session: Session, tenant_id, payload: dict, actor: str | None = None) -> Lead:
    """Validate, deduplicate-check and persist a new lead."""
    primary_mobile = normalize_phone(payload.get("primary_mobile"))
    if not primary_mobile:
        raise ValidationError("primary mobile is required")
    email = normalize_email(payload.get("primary_email"))
    source = (payload.get("lead_source") or "OTHER").upper()
    if source not in LEAD_SOURCES:
        raise ValueError(f"invalid lead source: {source}")
    lead_type = (payload.get("lead_type") or "INDIVIDUAL").upper()
    if lead_type not in LEAD_TYPES:
        raise ValueError(f"invalid lead type: {lead_type}")
    priority = (payload.get("priority") or "MEDIUM").upper()
    if priority not in LEAD_PRIORITIES:
        raise ValueError(f"invalid priority: {priority}")

    duplicates = find_duplicate_leads(session, tenant_id, primary_mobile, email)
    lead = Lead(
        tenant_id=tenant_id,
        lead_number="",  # assigned after flush
        lead_type=lead_type,
        first_name=(payload.get("first_name") or "").strip() or None,
        last_name=(payload.get("last_name") or "").strip() or None,
        company_name=(payload.get("company_name") or "").strip() or None,
        primary_mobile=primary_mobile,
        alternate_mobile=normalize_phone(payload.get("alternate_mobile")),
        primary_email=email,
        alternate_email=normalize_email(payload.get("alternate_email")),
        preferred_channel=(payload.get("preferred_channel") or "").upper() or None,
        requested_service=payload.get("requested_service"),
        requested_plan_reference=payload.get("requested_plan_reference"),
        expected_monthly_value=payload.get("expected_monthly_value"),
        installation_address_draft=payload.get("installation_address_draft") or {},
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        lead_source=source,
        campaign_reference=payload.get("campaign_reference"),
        referrer=payload.get("referrer"),
        franchise_id=payload.get("franchise_id"),
        branch_id=payload.get("branch_id"),
        area=payload.get("area"),
        assigned_salesperson_id=payload.get("assigned_salesperson_id"),
        assigned_team_id=payload.get("assigned_team_id"),
        priority=priority,
        stage="NEW",
        qualification_score=0,
        feasibility_state="UNKNOWN",
        next_followup_at=payload.get("next_followup_at"),
        sla_deadline=payload.get("sla_deadline"),
        created_by=actor,
    )
    session.add(lead)
    session.flush()
    lead.lead_number = _lead_number(lead)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.created", "lead", lead.id, safe_after={"lead_number": lead.lead_number, "lead_source": source, "stage": lead.stage, "duplicate_warnings": len(duplicates)}, correlation_id=request_id)
    outbox(session, "crm.lead.created.v1", tenant_id, request_id, {"lead_id": str(lead.id), "lead_number": lead.lead_number, "lead_source": source, "stage": lead.stage, "duplicate_warnings": len(duplicates)})
    timeline(session, tenant_id, "LEAD", f"Lead {lead.lead_number} captured", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return lead


def find_duplicate_leads(session: Session, tenant_id, mobile: str, email: str | None = None) -> list[Lead]:
    """Tenant-scoped duplicate detection by normalized mobile/email."""
    statement = select(Lead).where(Lead.tenant_id == tenant_id, Lead.primary_mobile == mobile)
    if email:
        statement = statement.union_all(select(Lead).where(Lead.tenant_id == tenant_id, Lead.primary_email == email))
    return list(session.scalars(statement))


def get_lead(session: Session, tenant_id, lead_id) -> Lead:
    lead = session.scalar(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    if lead is None:
        raise ValueError("lead not found")
    return lead


def assign_lead(session: Session, tenant_id, lead_id, assigned_to: str | None, method: str, reason: str | None, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    session.add(LeadAssignment(tenant_id=tenant_id, lead_id=lead.id, assigned_to=assigned_to, assigned_by=actor, method=method.upper(), reason=reason))
    lead.assigned_salesperson_id = assigned_to or lead.assigned_salesperson_id
    if lead.stage == "NEW":
        lead.stage = lead_transition(lead.stage, "ASSIGNED")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.assigned", "lead", lead.id, safe_after={"assigned_to": assigned_to, "method": method}, reason=reason, correlation_id=request_id)
    outbox(session, "crm.lead.assigned.v1", tenant_id, request_id, {"lead_id": str(lead.id), "assigned_to": assigned_to, "method": method})
    timeline(session, tenant_id, "LEAD", f"Lead assigned to {assigned_to or 'unassigned'}", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return lead


def transition_lead(session: Session, tenant_id, lead_id, to_stage: str, reason: str | None = None, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    to_stage = to_stage.upper()
    if to_stage not in LEAD_STAGES:
        raise ValueError(f"invalid lead stage: {to_stage}")
    from_stage = lead.stage
    lead.stage = lead_transition(from_stage, to_stage)
    if to_stage in {"LOST", "DISQUALIFIED"}:
        lead.lost_reason = reason if to_stage == "LOST" else lead.lost_reason
        lead.disqualification_reason = reason if to_stage == "DISQUALIFIED" else lead.disqualification_reason
    session.add(LeadStageHistory(tenant_id=tenant_id, lead_id=lead.id, from_stage=from_stage, to_stage=to_stage, actor=actor, reason=reason, correlation_id=correlation(None)))
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.stage_changed", "lead", lead.id, safe_before={"stage": from_stage}, safe_after={"stage": to_stage}, reason=reason, correlation_id=request_id)
    outbox(session, "crm.lead.stage_changed.v1", tenant_id, request_id, {"lead_id": str(lead.id), "from_stage": from_stage, "to_stage": to_stage})
    timeline(session, tenant_id, "LEAD", f"Lead stage {from_stage} -> {to_stage}", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return lead


def qualify_lead(session: Session, tenant_id, lead_id, score: int, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    if not 0 <= int(score) <= 100:
        raise ValueError("qualification score must be 0..100")
    lead.qualification_score = int(score)
    if lead.stage in {"NEW", "ASSIGNED", "CONTACTED"}:
        lead.stage = lead_transition(lead.stage, "QUALIFICATION")
    request_id = record_stage_after(session, tenant_id, lead, "qualification", score, actor)
    return lead


def record_stage_after(session: Session, tenant_id, lead: Lead, action: str, value, actor: str | None = None) -> str:
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", f"crm.lead.{action}", "lead", lead.id, safe_after={action: value}, correlation_id=request_id)
    session.flush()
    return request_id


def request_feasibility(session: Session, tenant_id, lead_id, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    if lead.feasibility_state == "PENDING":
        return lead
    lead.feasibility_state = "PENDING"
    if lead.stage not in {"FEASIBILITY_PENDING", "FEASIBLE", "NOT_FEASIBLE"}:
        lead.stage = lead_transition(lead.stage, "FEASIBILITY_PENDING")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.feasibility_requested", "lead", lead.id, safe_after={"feasibility_state": "PENDING"}, correlation_id=request_id)
    outbox(session, "crm.lead.feasibility_requested.v1", tenant_id, request_id, {"lead_id": str(lead.id), "installation_address": lead.installation_address_draft})
    timeline(session, tenant_id, "LEAD", "Feasibility requested from OSS", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return lead


def record_feasibility_result(session: Session, tenant_id, lead_id, feasible: bool, external_ref: str | None = None, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    lead.feasibility_state = "FEASIBLE" if feasible else "NOT_FEASIBLE"
    lead.feasibility_external_ref = external_ref
    if lead.stage == "FEASIBILITY_PENDING":
        lead.stage = lead_transition(lead.stage, "FEASIBLE" if feasible else "NOT_FEASIBLE")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.feasibility_result", "lead", lead.id, safe_after={"feasibility_state": lead.feasibility_state, "external_ref": external_ref}, correlation_id=request_id)
    timeline(session, tenant_id, "LEAD", f"Feasibility {'feasible' if feasible else 'not feasible'}", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return lead


def add_interaction(session: Session, tenant_id, lead_id, payload: dict, actor: str | None = None) -> LeadInteraction:
    lead = get_lead(session, tenant_id, lead_id)
    channel = (payload.get("channel") or "").upper()
    if channel not in INTERACTION_CHANNELS:
        raise ValueError(f"invalid interaction channel: {channel}")
    interaction = LeadInteraction(
        tenant_id=tenant_id, lead_id=lead.id, actor=actor,
        direction=(payload.get("direction") or "INBOUND").upper(),
        channel=channel, subject=payload.get("subject"), safe_summary=payload.get("safe_summary"),
        outcome=payload.get("outcome"), next_action=payload.get("next_action"),
        scheduled_at=payload.get("scheduled_at"), completed_at=payload.get("completed_at") or datetime.now(timezone.utc),
        status=(payload.get("status") or "COMPLETED").upper(),
        external_communication_id=payload.get("external_communication_id"),
    )
    session.add(interaction)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.interaction", "lead", lead.id, safe_after={"channel": channel, "direction": interaction.direction}, correlation_id=request_id)
    timeline(session, tenant_id, "INTERACTION", f"{channel} interaction logged", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return interaction


def schedule_followup(session: Session, tenant_id, lead_id, payload: dict, actor: str | None = None) -> FollowUp:
    lead = get_lead(session, tenant_id, lead_id)
    followup = FollowUp(
        tenant_id=tenant_id, lead_id=lead.id, subject=payload.get("subject"), safe_summary=payload.get("safe_summary"),
        scheduled_at=payload.get("scheduled_at"), assigned_to=payload.get("assigned_to"), status="PENDING", created_by=actor,
    )
    if followup.scheduled_at is None:
        raise ValueError("follow-up scheduled_at is required")
    session.add(followup)
    lead.next_followup_at = followup.scheduled_at
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.followup.created", "followup", followup.id, safe_after={"scheduled_at": followup.scheduled_at.isoformat()}, correlation_id=request_id)
    timeline(session, tenant_id, "FOLLOW_UP", "Follow-up scheduled", actor=actor, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return followup


def complete_followup(session: Session, tenant_id, followup_id, actor: str | None = None) -> FollowUp:
    followup = get_followup(session, tenant_id, followup_id)
    followup.status = "COMPLETED"
    followup.completed_at = datetime.now(timezone.utc)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.followup.completed", "followup", followup.id, safe_after={"status": "COMPLETED"}, correlation_id=request_id)
    session.flush()
    return followup


def reschedule_followup(session: Session, tenant_id, followup_id, scheduled_at, actor: str | None = None) -> FollowUp:
    followup = get_followup(session, tenant_id, followup_id)
    followup.scheduled_at = scheduled_at
    followup.status = "RESCHEDULED"
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.followup.rescheduled", "followup", followup.id, safe_after={"scheduled_at": scheduled_at.isoformat()}, correlation_id=request_id)
    session.flush()
    return followup


def get_followup(session: Session, tenant_id, followup_id) -> FollowUp:
    followup = session.scalar(select(FollowUp).where(FollowUp.id == followup_id, FollowUp.tenant_id == tenant_id))
    if followup is None:
        raise ValueError("follow-up not found")
    return followup


def reopen_lead(session: Session, tenant_id, lead_id, actor: str | None = None) -> Lead:
    lead = get_lead(session, tenant_id, lead_id)
    if lead.stage not in {"LOST", "DISQUALIFIED", "DUPLICATE"}:
        raise ValueError("only lost, disqualified or duplicate leads can be reopened")
    lead.stage = lead_transition(lead.stage, "NEW")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.lead.reopened", "lead", lead.id, safe_after={"stage": "NEW"}, correlation_id=request_id)
    session.flush()
    return lead


def lead_history(session: Session, tenant_id, lead_id) -> list[LeadStageHistory]:
    get_lead(session, tenant_id, lead_id)
    return list(session.scalars(select(LeadStageHistory).where(LeadStageHistory.lead_id == lead_id, LeadStageHistory.tenant_id == tenant_id).order_by(LeadStageHistory.created_at.desc())))
