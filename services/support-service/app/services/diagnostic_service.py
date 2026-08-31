"""Diagnostic snapshot service.

Builds a tenant-aware diagnostic context from CRM, BSS, OSS, AAA, Network
Control and NMS adapters and runs deterministic, explainable checks. Missing or
unavailable dependency data is reported as unavailable — it is never pretended
to be healthy. Snapshots are stored so later refreshes are comparable."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError
from ..integrations.base import get_adapter
from ..models import Ticket, TicketDiagnosticSnapshot
from ..services.audit_service import append_event, correlation
from . import ticket_service

# Suggested action + required permission for each check.
_CHECK_META = {
    "financial_suspension": ("Request billing review", "support.billing.summary.view"),
    "service_suspension": ("Request service reactivation review", "support.action.request"),
    "no_active_session": ("Re-run diagnostics / disconnect-reauth", "support.diagnostic.run"),
    "recent_auth_rejects": ("Review PPPoE credentials / disconnect-reauth", "support.diagnostic.run"),
    "stale_accounting": ("Request AAA reconciliation", "support.action.request"),
    "speed_mismatch": ("Reapply session policy", "support.action.request"),
    "fup_throttling": ("Check FUP usage and policy", "support.diagnostic.view"),
    "ip_mismatch": ("Request IP assignment reconciliation", "support.action.request"),
    "nas_unreachable": ("Open NOC investigation", "support.action.request"),
    "known_outage": ("Link outage and communicate status", "support.outage.link"),
    "ont_offline": ("Schedule field visit", "support.action.request"),
    "los_alarm": ("Schedule field visit", "support.action.request"),
    "order_incomplete": ("Retry approved provisioning step", "support.action.request"),
    "restoration_pending": ("Request payment reconciliation", "support.action.request"),
    "duplicate_sessions": ("Disconnect and reauthorize session", "support.action.request"),
    "config_drift": ("Reapply session policy", "support.action.request"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def gather_context(session: Session, ticket: Ticket, *, include_payment_detail: bool = False) -> dict:
    crm = get_adapter("crm")
    bss = get_adapter("bss")
    oss = get_adapter("oss")
    aaa = get_adapter("aaa")
    network = get_adapter("network")
    nms = get_adapter("nms")

    sources = {}
    for name, result in [
        ("crm", crm.get_customer_context(ticket.customer_id) if ticket.customer_id else None),
        ("bss", bss.get_billing_context(ticket.billing_account_id, ticket.customer_id, include_payment_detail)),
        ("oss", oss.get_subscriber_context(ticket.service_subscription_id, ticket.subscriber_username)),
        ("aaa", aaa.get_session_context(ticket.subscriber_username, None)),
        ("network", network.get_policy_context(ticket.subscriber_username, ticket.service_subscription_id)),
        ("nms", nms.get_device_context(None, None, ticket.service_location_id)),
    ]:
        if result is None:
            sources[name] = {"status": "SKIPPED", "freshness": "n/a",
                             "reason": "no reference provided", "data": {}}
            continue
        if result.ok:
            sources[name] = {"status": "COMPLETE", "freshness": "fresh",
                             "timestamp": _iso(datetime.now(timezone.utc)), "data": result.output}
        else:
            sources[name] = {"status": "FAILED", "freshness": "unavailable",
                             "error_code": result.error_code, "error_detail": result.error_detail,
                             "retryable": result.retryable, "timestamp": _iso(datetime.now(timezone.utc)),
                             "data": {}}
    overall = "COMPLETE"
    statuses = [s["status"] for s in sources.values()]
    if "FAILED" in statuses or "SKIPPED" in statuses:
        overall = "PARTIAL" if any(s == "COMPLETE" for s in statuses) else "FAILED"
    return {"captured_at": _iso(datetime.now(timezone.utc)), "status": overall, "sources": sources}


def run_diagnostic_checks(context: dict) -> list[dict]:
    """Deterministic, explainable checks over the assembled context."""
    sources = context.get("sources", {})
    checks: list[dict] = []
    now = datetime.now(timezone.utc)

    def add(name: str, status: str, severity: str, evidence: str, source: str, confidence: str = "high"):
        suggested, permission = _CHECK_META.get(name, ("Contact support", "support.diagnostic.view"))
        checks.append({
            "name": name, "status": status, "severity": severity, "evidence": evidence,
            "source": source, "timestamp": _iso(now), "suggested_action": suggested,
            "required_permission": permission, "confidence": confidence,
        })

    def data_of(source: str) -> dict:
        entry = sources.get(source, {})
        return entry.get("data", {}) if entry.get("status") == "COMPLETE" else {}

    bss = data_of("bss")
    oss = data_of("oss")
    aaa = data_of("aaa")
    network = data_of("network")
    nms = data_of("nms")

    # BSS
    if sources.get("bss", {}).get("status") == "COMPLETE":
        if bss.get("financial_restriction"):
            add("financial_suspension", "WARN", "high", f"financial restriction: {bss['financial_restriction']}", "bss")
        if str(bss.get("billing_status", "")).upper() in ("DELINQUENT", "SUSPENDED"):
            add("financial_suspension", "FAIL", "critical", f"billing status {bss.get('billing_status')}", "bss")
        # Payment captured but restoration pending — evidence from bss + oss.
        if bss.get("invoice_summary") and oss.get("suspension_state") and str(oss.get("suspension_state", "")).upper() not in ("NONE",):
            add("restoration_pending", "WARN", "medium",
                "payment captured recently but service remains suspended", "bss", confidence="medium")
    else:
        add("financial_suspension", "UNKNOWN", "medium", "bss unavailable", "bss", confidence="low")

    # OSS
    if sources.get("oss", {}).get("status") == "COMPLETE":
        if str(oss.get("suspension_state", "")).upper() not in ("NONE", ""):
            add("service_suspension", "WARN", "high", f"suspension state {oss.get('suspension_state')}", "oss")
        orders = oss.get("recent_orders") or []
        incomplete = [o for o in orders if str(o.get("state", "")).upper() not in ("COMPLETED", "DONE")]
        if incomplete:
            add("order_incomplete", "WARN", "medium", f"{len(incomplete)} order(s) not completed", "oss")
    else:
        add("service_suspension", "UNKNOWN", "medium", "oss unavailable", "oss", confidence="low")

    # AAA
    if sources.get("aaa", {}).get("status") == "COMPLETE":
        sessions = aaa.get("active_sessions") or []
        if not sessions:
            add("no_active_session", "WARN", "high", "no active PPPoE/hotspot session", "aaa")
        if aaa.get("auth_failures"):
            add("recent_auth_rejects", "FAIL", "critical", f"{len(aaa['auth_failures'])} recent reject(s)", "aaa")
        if aaa.get("last_auth_result") in ("REJECT", "FAIL"):
            add("recent_auth_rejects", "FAIL", "critical", f"last auth {aaa['last_auth_result']}", "aaa")
        if len(sessions) > 1:
            add("duplicate_sessions", "WARN", "medium", f"{len(sessions)} active sessions", "aaa")
        if oss.get("assigned_ip"):
            ips = {s.get("framed_ip") for s in sessions if s.get("framed_ip")}
            if ips and oss.get("assigned_ip") not in ips:
                add("ip_mismatch", "WARN", "high",
                    f"assigned {oss.get('assigned_ip')} not in active sessions", "aaa")
    else:
        add("no_active_session", "UNKNOWN", "medium", "aaa unavailable", "aaa", confidence="low")

    # Network control
    if sources.get("network", {}).get("status") == "COMPLETE":
        expected = network.get("expected_bandwidth")
        applied = network.get("applied_bandwidth")
        if expected and applied and int(applied) < int(expected):
            add("speed_mismatch", "FAIL", "high", f"applied {applied} < expected {expected}", "network")
        if network.get("fup_state") and str(network.get("fup_state", "")).upper() not in ("NONE",):
            add("fup_throttling", "WARN", "medium", f"FUP state {network.get('fup_state')}", "network")
        if network.get("policy_drift"):
            add("config_drift", "WARN", "high", "router configuration drift detected", "network")
    else:
        add("speed_mismatch", "UNKNOWN", "medium", "network unavailable", "network", confidence="low")

    # NMS
    if sources.get("nms", {}).get("status") == "COMPLETE":
        if str(nms.get("nas_health", "UP")).upper() != "UP":
            add("nas_unreachable", "FAIL", "critical", f"NAS health {nms.get('nas_health')}", "nms")
        if nms.get("known_outage"):
            add("known_outage", "FAIL", "critical", f"known outage {nms.get('known_outage')}", "nms")
        if str(nms.get("onu_health", "UP")).upper() != "UP":
            add("ont_offline", "WARN", "high", f"ONT health {nms.get('onu_health')}", "nms")
        alarms = [str(a).upper() for a in (nms.get("recent_alarms") or [])]
        if any("LOS" in a for a in alarms):
            add("los_alarm", "WARN", "high", "LOS alarm present", "nms")
    else:
        add("known_outage", "UNKNOWN", "medium", "nms unavailable", "nms", confidence="low")

    return checks


def capture_diagnostic_snapshot(session: Session, tenant_id, ticket: Ticket, *, actor: str | None = None,
                                correlation_id: str | None = None, emit_event: bool = True) -> TicketDiagnosticSnapshot:
    if ticket.tenant_id != tenant_id:
        raise NotFoundError("ticket not found")
    context = gather_context(session, ticket)
    checks = run_diagnostic_checks(context)
    context["checks"] = checks
    snapshot = TicketDiagnosticSnapshot(
        tenant_id=tenant_id, ticket_id=ticket.id, status=context["status"],
        snapshot=context, captured_by=actor, correlation_id=correlation_id or correlation(None),
    )
    session.add(snapshot)
    session.flush()
    if emit_event:
        append_event(session, ticket, "ticket.diagnostic_snapshot_captured",
                     payload={"snapshot_id": str(snapshot.id), "status": context["status"],
                              "check_count": len(checks), "failed_sources": [
                                  k for k, v in context["sources"].items() if v.get("status") == "FAILED"]},
                     actor_type="agent" if actor else "system", actor_id=actor or "system",
                     correlation_id=correlation_id or ticket.correlation_id)
    return snapshot


def latest_snapshot(session: Session, ticket_id) -> TicketDiagnosticSnapshot | None:
    from sqlalchemy import select

    return session.scalars(
        select(TicketDiagnosticSnapshot).where(TicketDiagnosticSnapshot.ticket_id == ticket_id)
        .order_by(TicketDiagnosticSnapshot.captured_at.desc())
    ).first()
